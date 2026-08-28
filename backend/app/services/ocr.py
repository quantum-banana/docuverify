"""Local raster OCR providers with truthful capability and failure reporting."""

from __future__ import annotations

import importlib.util
import os
import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import numpy as np


@dataclass(frozen=True, slots=True)
class OCRWord:
    text: str
    bbox: tuple[float, float, float, float]
    confidence: float


@dataclass(frozen=True, slots=True)
class OCRResult:
    text: str
    words: tuple[OCRWord, ...]
    provider: str
    device: str
    confidence: float | None
    succeeded: bool
    error: str | None = None


class RasterOCRProvider(Protocol):
    name: str
    device: str

    @property
    def available(self) -> bool: ...

    @property
    def initialization_count(self) -> int: ...

    def extract(self, image: np.ndarray) -> OCRResult: ...


class RapidOCRProvider:
    """Cached ONNX Runtime CPU provider shared across pages and analyses."""

    name = "rapidocr_onnxruntime"
    device = "cpu"

    def __init__(self) -> None:
        self._engine: object | None = None
        self._engine_lock = threading.Lock()
        self._inference_lock = threading.Lock()
        self._initialization_count = 0

    @property
    def available(self) -> bool:
        return (
            importlib.util.find_spec("rapidocr") is not None
            and importlib.util.find_spec("onnxruntime") is not None
        )

    @property
    def initialization_count(self) -> int:
        return self._initialization_count

    def _get_engine(self) -> object:
        if self._engine is not None:
            return self._engine
        with self._engine_lock:
            if self._engine is None:
                if not self.available:
                    raise RuntimeError("RapidOCR with ONNX Runtime is not installed")
                from rapidocr import RapidOCR

                self._engine = RapidOCR()
                self._initialization_count += 1
        return self._engine

    def extract(self, image: np.ndarray) -> OCRResult:
        if image.size == 0:
            return _failed(self.name, self.device, "empty raster page")
        try:
            engine = self._get_engine()
            with self._inference_lock:
                output = engine(image)
            raw_texts = list(output.txts) if output.txts is not None else []
            raw_boxes = list(output.boxes) if output.boxes is not None else []
            raw_scores = list(output.scores) if output.scores is not None else []
            height, width = image.shape[:2]
            words: list[OCRWord] = []
            for raw_text, raw_box, raw_score in zip(
                raw_texts, raw_boxes, raw_scores, strict=False
            ):
                text = str(raw_text).strip()
                points = np.asarray(raw_box, dtype=np.float32).reshape(-1, 2)
                if not text or points.size == 0:
                    continue
                x0 = _unit(float(points[:, 0].min()) / max(1, width))
                y0 = _unit(float(points[:, 1].min()) / max(1, height))
                x1 = _unit(float(points[:, 0].max()) / max(1, width))
                y1 = _unit(float(points[:, 1].max()) / max(1, height))
                if x1 <= x0 or y1 <= y0:
                    continue
                words.append(
                    OCRWord(
                        text=text,
                        bbox=(x0, y0, x1, y1),
                        confidence=_unit(float(raw_score)),
                    )
                )
            if not words:
                return _failed(self.name, self.device, "no raster text detected")
            confidence = sum(word.confidence for word in words) / len(words)
            return OCRResult(
                text="\n".join(word.text for word in words),
                words=tuple(words),
                provider=self.name,
                device=self.device,
                confidence=confidence,
                succeeded=True,
            )
        except Exception as exc:  # OCR must not prevent visual analysis.
            return _failed(self.name, self.device, type(exc).__name__)


class UnavailableOCRProvider:
    name = "unavailable_for_raster"
    device = "cpu"
    available = False
    initialization_count = 0

    def extract(self, image: np.ndarray) -> OCRResult:
        del image
        return _failed(self.name, self.device, "provider unavailable")


def get_raster_ocr_provider(preference: str | None = None) -> RasterOCRProvider:
    selected = (preference or os.getenv("DOCUVERIFY_OCR_PROVIDER", "auto")).strip().lower()
    return _get_raster_ocr_provider(selected)


@lru_cache(maxsize=3)
def _get_raster_ocr_provider(selected: str) -> RasterOCRProvider:
    if selected == "none":
        return UnavailableOCRProvider()
    provider = RapidOCRProvider()
    if provider.available:
        return provider
    return UnavailableOCRProvider()


def raster_ocr_capability(preference: str | None = None) -> tuple[bool, str, str]:
    provider = get_raster_ocr_provider(preference)
    return provider.available, provider.name, provider.device


def _failed(provider: str, device: str, error: str) -> OCRResult:
    return OCRResult(
        text="",
        words=(),
        provider=provider,
        device=device,
        confidence=None,
        succeeded=False,
        error=error,
    )


def _unit(value: float) -> float:
    return max(0.0, min(1.0, value))
