from __future__ import annotations

import json
from pathlib import Path

import fitz
import numpy as np

from backend.app.services.documents import (
    PyMuPDFEmbeddedTextProvider,
    TextExtractor,
    extract_page_text,
    render_document_page,
    validate_upload,
)
from backend.app.services.ocr import OCRResult, get_raster_ocr_provider, raster_ocr_capability


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLES = PROJECT_ROOT / "samples"
PHASE2_MANIFEST = json.loads(
    (SAMPLES / "expected" / "phase2_manifest.json").read_text(encoding="utf-8")
)


def test_raster_only_pdf_returns_expected_tokens_and_normalized_boxes() -> None:
    fixture = SAMPLES / PHASE2_MANIFEST["raster_ocr"]["pdf"]
    data = fixture.read_bytes()
    with fitz.open(stream=data, filetype="pdf") as document:
        assert document[0].get_text("text").strip() == ""

    upload = validate_upload(
        field="candidate",
        filename=fixture.name,
        content_type="application/pdf",
        data=data,
        max_bytes=20 * 1024 * 1024,
    )
    rendered = render_document_page(upload, 0, 1800)
    extraction = extract_page_text(upload, rendered, 0)

    assert extraction.succeeded is True
    assert extraction.source == "rapidocr_onnxruntime"
    assert extraction.device == "cpu"
    assert extraction.confidence is not None and extraction.confidence >= 0.8
    assert len(extraction.words) >= PHASE2_MANIFEST["raster_ocr"]["minimum_token_matches"]

    matches = 0
    minimum_iou = PHASE2_MANIFEST["raster_ocr"]["approximate_box_minimum_iou"]
    for expected in PHASE2_MANIFEST["raster_ocr"]["expected_tokens"]:
        token = expected["text"].casefold()
        candidates = [word for word in extraction.words if token in word.text.casefold()]
        if not candidates:
            continue
        expected_box = expected["normalized_bbox"]
        expected_xyxy = (
            expected_box["x"],
            expected_box["y"],
            expected_box["x"] + expected_box["width"],
            expected_box["y"] + expected_box["height"],
        )
        assert max(_iou(expected_xyxy, word.bbox) for word in candidates) >= minimum_iou
        matches += 1
    assert matches >= PHASE2_MANIFEST["raster_ocr"]["minimum_token_matches"]


def test_raster_provider_is_cached_across_pages() -> None:
    fixture = SAMPLES / PHASE2_MANIFEST["raster_ocr"]["png"]
    upload = validate_upload(
        field="candidate",
        filename=fixture.name,
        content_type="image/png",
        data=fixture.read_bytes(),
        max_bytes=20 * 1024 * 1024,
    )
    rendered = render_document_page(upload, 0, 1800)
    provider = get_raster_ocr_provider()
    before = provider.initialization_count

    first = extract_page_text(upload, rendered, 0)
    second = extract_page_text(upload, rendered, 0)

    assert first.succeeded and second.succeeded
    assert get_raster_ocr_provider() is provider
    assert provider.initialization_count == max(1, before)
    assert provider.initialization_count == 1


def test_raster_ocr_failure_is_truthful_and_lowers_text_coverage() -> None:
    class FailingProvider:
        name = "failing_test_provider"
        device = "cpu"
        available = True
        initialization_count = 1

        def extract(self, _image: object) -> OCRResult:
            return OCRResult(
                text="",
                words=(),
                provider=self.name,
                device=self.device,
                confidence=None,
                succeeded=False,
                error="simulated failure",
            )

    fixture = SAMPLES / PHASE2_MANIFEST["raster_ocr"]["png"]
    upload = validate_upload(
        field="candidate",
        filename=fixture.name,
        content_type="image/png",
        data=fixture.read_bytes(),
        max_bytes=20 * 1024 * 1024,
    )
    rendered = render_document_page(upload, 0, 1800)
    extraction = TextExtractor(
        PyMuPDFEmbeddedTextProvider(), FailingProvider()
    ).extract(upload, page_index=0, rendered=rendered)

    assert extraction.succeeded is False
    assert extraction.source == "failing_test_provider"
    assert extraction.coverage == 0.0
    assert extraction.confidence is None
    assert extraction.error == "simulated failure"
    assert rendered.image.size > 0


def test_raster_ocr_can_be_explicitly_disabled() -> None:
    capable, provider_name, device = raster_ocr_capability("none")
    provider = get_raster_ocr_provider("none")

    assert capable is False
    assert provider_name == "unavailable_for_raster"
    assert device == "cpu"
    assert provider.extract(np.ones((8, 8, 3), dtype=np.uint8)).succeeded is False


def _iou(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    x0 = max(left[0], right[0])
    y0 = max(left[1], right[1])
    x1 = min(left[2], right[2])
    y1 = min(left[3], right[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0
