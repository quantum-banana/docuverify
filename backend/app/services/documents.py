"""Strict single-page validation, rendering, normalization, and text extraction."""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import cv2
import fitz
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from backend.app.models.contracts import CoordinateTransform


SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
MIME_BY_EXTENSION = {
    ".pdf": {"application/pdf", "application/x-pdf"},
    ".png": {"image/png"},
    ".jpg": {"image/jpeg", "image/jpg"},
    ".jpeg": {"image/jpeg", "image/jpg"},
}
MAX_IMAGE_PIXELS = 40_000_000


class DocumentValidationError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        field: str,
        details: dict[str, str | int] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field
        self.details = details or {}


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    field: str
    filename: str
    content_type: str
    extension: str
    kind: Literal["pdf", "image"]
    data: bytes
    sha256: str
    page_count: Literal[1] = 1


@dataclass(slots=True)
class RenderedDocument:
    image: np.ndarray
    transform: CoordinateTransform


@dataclass(frozen=True, slots=True)
class TextWord:
    text: str
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class TextExtraction:
    text: str
    words: tuple[TextWord, ...]
    source: str
    confidence: float | None


class TextProvider(Protocol):
    """Extension point for embedded text or a later local raster OCR engine."""

    name: str
    device: str

    def supports(self, upload: ValidatedUpload) -> bool: ...

    def extract(self, upload: ValidatedUpload) -> TextExtraction: ...


class PyMuPDFEmbeddedTextProvider:
    name = "pymupdf_embedded_text"
    device = "cpu"

    def supports(self, upload: ValidatedUpload) -> bool:
        return upload.kind == "pdf"

    def extract(self, upload: ValidatedUpload) -> TextExtraction:
        with fitz.open(stream=upload.data, filetype="pdf") as document:
            page = document.load_page(0)
            width, height = page.rect.width, page.rect.height
            extracted_words: list[TextWord] = []
            for word in page.get_text("words", sort=True):
                x0, y0, x1, y1, text = word[:5]
                if not str(text).strip():
                    continue
                extracted_words.append(
                    TextWord(
                        text=str(text),
                        bbox=(
                            max(0.0, min(1.0, float(x0 / width))),
                            max(0.0, min(1.0, float(y0 / height))),
                            max(0.0, min(1.0, float(x1 / width))),
                            max(0.0, min(1.0, float(y1 / height))),
                        ),
                    )
                )
            text = page.get_text("text", sort=True).strip()
            source = self.name if text else "no_embedded_text"
            return TextExtraction(
                text=text,
                words=tuple(extracted_words),
                source=source,
                confidence=1.0 if text else None,
            )


class UnavailableRasterTextProvider:
    name = "unavailable_for_raster"
    device = "cpu"

    def supports(self, upload: ValidatedUpload) -> bool:
        return upload.kind == "image"

    def extract(self, upload: ValidatedUpload) -> TextExtraction:
        return TextExtraction(text="", words=(), source=self.name, confidence=None)


class TextExtractor:
    def __init__(self, providers: tuple[TextProvider, ...]) -> None:
        self.providers = providers

    def extract(self, upload: ValidatedUpload) -> TextExtraction:
        for provider in self.providers:
            if provider.supports(upload):
                return provider.extract(upload)
        return TextExtraction(text="", words=(), source="unavailable", confidence=None)


DEFAULT_TEXT_EXTRACTOR = TextExtractor(
    (PyMuPDFEmbeddedTextProvider(), UnavailableRasterTextProvider())
)


def sanitize_filename(filename: str | None, field: str) -> str:
    raw = (filename or f"{field}.bin").replace("\\", "/").split("/")[-1]
    stem = Path(raw).stem
    suffix = Path(raw).suffix.lower()
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or field
    return f"{safe_stem[:80]}{suffix}"


def validate_upload(
    *,
    field: str,
    filename: str | None,
    content_type: str | None,
    data: bytes,
    max_bytes: int,
) -> ValidatedUpload:
    safe_name = sanitize_filename(filename, field)
    extension = Path(safe_name).suffix.lower()
    if not data:
        raise DocumentValidationError(
            "empty_file", f"The {field} file is empty.", field=field
        )
    if len(data) > max_bytes:
        raise DocumentValidationError(
            "file_too_large",
            f"The {field} file exceeds the configured upload limit.",
            field=field,
            details={"max_bytes": max_bytes, "received_bytes": len(data)},
        )
    if extension not in SUPPORTED_EXTENSIONS:
        raise DocumentValidationError(
            "unsupported_file_type",
            f"The {field} file extension must be PDF, PNG, JPG, or JPEG.",
            field=field,
            details={"extension": extension or "none"},
        )
    declared_mime = (content_type or "").split(";", 1)[0].strip().lower()
    if declared_mime not in MIME_BY_EXTENSION[extension]:
        raise DocumentValidationError(
            "mime_type_mismatch",
            f"The declared MIME type does not match the {field} file extension.",
            field=field,
            details={"content_type": declared_mime or "missing", "extension": extension},
        )

    if extension == ".pdf":
        _validate_pdf(data, field)
        kind: Literal["pdf", "image"] = "pdf"
        canonical_mime = "application/pdf"
    else:
        _validate_image(data, field, extension)
        kind = "image"
        canonical_mime = "image/png" if extension == ".png" else "image/jpeg"
    return ValidatedUpload(
        field=field,
        filename=safe_name,
        content_type=canonical_mime,
        extension=extension,
        kind=kind,
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _validate_pdf(data: bytes, field: str) -> None:
    if not data.startswith(b"%PDF-"):
        raise DocumentValidationError(
            "content_type_mismatch",
            f"The {field} file does not contain a valid PDF signature.",
            field=field,
        )
    try:
        with fitz.open(stream=data, filetype="pdf") as document:
            if not document.is_pdf or document.is_repaired:
                raise DocumentValidationError(
                    "corrupt_pdf",
                    f"The {field} PDF is corrupt or required structural repair.",
                    field=field,
                )
            if document.needs_pass:
                raise DocumentValidationError(
                    "encrypted_pdf",
                    f"The {field} PDF is password protected.",
                    field=field,
                )
            page_count = document.page_count
            if page_count != 1:
                raise DocumentValidationError(
                    "single_page_required",
                    f"Phase 1 accepts exactly one page; the {field} PDF has {page_count} pages.",
                    field=field,
                    details={"page_count": page_count},
                )
            page = document.load_page(0)
            if page.rect.width <= 0 or page.rect.height <= 0:
                raise DocumentValidationError(
                    "corrupt_pdf", f"The {field} PDF has an invalid page size.", field=field
                )
    except DocumentValidationError:
        raise
    except Exception as exc:
        raise DocumentValidationError(
            "corrupt_pdf", f"The {field} PDF could not be read.", field=field
        ) from exc


def _validate_image(data: bytes, field: str, extension: str) -> None:
    signature_matches = (
        data.startswith(b"\x89PNG\r\n\x1a\n")
        if extension == ".png"
        else data.startswith(b"\xff\xd8\xff")
    )
    if not signature_matches:
        raise DocumentValidationError(
            "content_type_mismatch",
            f"The {field} contents do not match the file extension.",
            field=field,
        )
    try:
        with Image.open(io.BytesIO(data)) as image:
            if getattr(image, "n_frames", 1) != 1:
                raise DocumentValidationError(
                    "single_page_required",
                    f"Phase 1 accepts only a single-frame {field} image.",
                    field=field,
                )
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                raise DocumentValidationError(
                    "invalid_image_dimensions",
                    f"The {field} image dimensions are invalid or too large.",
                    field=field,
                    details={"width": width, "height": height},
                )
            image.verify()
    except DocumentValidationError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise DocumentValidationError(
            "corrupt_image", f"The {field} image could not be decoded.", field=field
        ) from exc


def save_upload(upload: ValidatedUpload, job_dir: Path, role: str) -> Path:
    input_dir = job_dir / "inputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    path = input_dir / f"{role}{upload.extension}"
    path.write_bytes(upload.data)
    return path


def render_document(upload: ValidatedUpload, max_dimension: int) -> RenderedDocument:
    if upload.kind == "pdf":
        return _render_pdf(upload.data, max_dimension)
    return _render_image(upload.data, max_dimension)


def _render_pdf(data: bytes, max_dimension: int) -> RenderedDocument:
    with fitz.open(stream=data, filetype="pdf") as document:
        page = document.load_page(0)
        rect = page.rect
        scale = max_dimension / max(rect.width, rect.height)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, colorspace=fitz.csRGB)
        rgb = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, 3)
        image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        transform = CoordinateTransform(
            original_width=max(1, round(rect.width)),
            original_height=max(1, round(rect.height)),
            normalized_width=pixmap.width,
            normalized_height=pixmap.height,
            scale_x=pixmap.width / rect.width,
            scale_y=pixmap.height / rect.height,
        )
        return RenderedDocument(image=image.copy(), transform=transform)


def _render_image(data: bytes, max_dimension: int) -> RenderedDocument:
    with Image.open(io.BytesIO(data)) as source:
        original_width, original_height = source.size
        orientation = int(source.getexif().get(274, 1))
        orientation_degrees = {3: 180, 6: 90, 8: 270}.get(orientation, 0)
        normalized = ImageOps.exif_transpose(source).convert("RGB")
        oriented_width, oriented_height = normalized.size
        scale = min(1.0, max_dimension / max(oriented_width, oriented_height))
        if scale < 1.0:
            target = (
                max(1, round(oriented_width * scale)),
                max(1, round(oriented_height * scale)),
            )
            normalized = normalized.resize(target, Image.Resampling.LANCZOS)
        rgb = np.asarray(normalized)
        image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    height, width = image.shape[:2]
    transform = CoordinateTransform(
        original_width=original_width,
        original_height=original_height,
        normalized_width=width,
        normalized_height=height,
        scale_x=width / oriented_width,
        scale_y=height / oriented_height,
        orientation_degrees=orientation_degrees,
    )
    return RenderedDocument(image=image, transform=transform)


def extract_text(upload: ValidatedUpload) -> TextExtraction:
    return DEFAULT_TEXT_EXTRACTOR.extract(upload)


def write_png(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    success, encoded = cv2.imencode(".png", image, [cv2.IMWRITE_PNG_COMPRESSION, 6])
    if not success:
        raise RuntimeError(f"Could not encode PNG asset {path.name}")
    path.write_bytes(encoded.tobytes())
