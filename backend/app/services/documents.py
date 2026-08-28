"""Bounded multi-page validation, rendering, and truthful text extraction."""

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
from backend.app.services.ocr import RasterOCRProvider, get_raster_ocr_provider


SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
MIME_BY_EXTENSION = {
    ".pdf": {"application/pdf", "application/x-pdf"},
    ".png": {"image/png"},
    ".jpg": {"image/jpeg", "image/jpg"},
    ".jpeg": {"image/jpeg", "image/jpg"},
}
MAX_IMAGE_PIXELS = 40_000_000
MAX_PAGES = 10
EMBEDDED_TEXT_MIN_CHARACTERS = 8
CONTENT_PREVIEW_MAX_DIMENSION = 512
CONTENT_CONTRAST_THRESHOLD = 12
CONTENT_EDGE_THRESHOLD = 24
CONTENT_MIN_PIXEL_RATIO = 0.0002
CONTENT_MIN_EDGE_RATIO = 0.00005


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
    page_count: int = 1


@dataclass(slots=True)
class RenderedDocument:
    image: np.ndarray
    transform: CoordinateTransform


@dataclass(frozen=True, slots=True)
class TextWord:
    text: str
    bbox: tuple[float, float, float, float]
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class TextExtraction:
    text: str
    words: tuple[TextWord, ...]
    source: str
    confidence: float | None
    device: str = "cpu"
    succeeded: bool = True
    coverage: float = 1.0
    error: str | None = None


class TextProvider(Protocol):
    """Extension point for embedded text extraction."""

    name: str
    device: str

    def supports(self, upload: ValidatedUpload) -> bool: ...

    def extract(self, upload: ValidatedUpload, page_index: int = 0) -> TextExtraction: ...


class PyMuPDFEmbeddedTextProvider:
    name = "pymupdf_embedded_text"
    device = "cpu"

    def supports(self, upload: ValidatedUpload) -> bool:
        return upload.kind == "pdf"

    def extract(self, upload: ValidatedUpload, page_index: int = 0) -> TextExtraction:
        with fitz.open(stream=upload.data, filetype="pdf") as document:
            page = document.load_page(page_index)
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
                        confidence=1.0,
                    )
                )
            text = page.get_text("text", sort=True).strip()
            reliable = (
                len(re.sub(r"\W+", "", text, flags=re.UNICODE))
                >= EMBEDDED_TEXT_MIN_CHARACTERS
                and bool(extracted_words)
            )
            return TextExtraction(
                text=text if reliable else "",
                words=tuple(extracted_words) if reliable else (),
                source=self.name if reliable else "no_embedded_text",
                confidence=1.0 if reliable else None,
                succeeded=reliable,
                coverage=1.0 if reliable else 0.0,
                error=None if reliable else "embedded text was absent or unreliable",
            )


class TextExtractor:
    def __init__(
        self,
        embedded_provider: TextProvider,
        raster_provider: RasterOCRProvider,
    ) -> None:
        self.embedded_provider = embedded_provider
        self.raster_provider = raster_provider

    def extract(
        self,
        upload: ValidatedUpload,
        *,
        page_index: int = 0,
        rendered: RenderedDocument | None = None,
    ) -> TextExtraction:
        _require_page_index(upload, page_index)
        if self.embedded_provider.supports(upload):
            embedded = self.embedded_provider.extract(upload, page_index)
            if embedded.succeeded:
                return embedded
        if rendered is None:
            rendered = render_document_page(upload, page_index, 1800)
        raster = self.raster_provider.extract(rendered.image)
        return TextExtraction(
            text=raster.text,
            words=tuple(
                TextWord(
                    text=word.text,
                    bbox=word.bbox,
                    confidence=word.confidence,
                )
                for word in raster.words
            ),
            source=raster.provider,
            confidence=raster.confidence,
            device=raster.device,
            succeeded=raster.succeeded,
            coverage=(raster.confidence or 0.0) if raster.succeeded else 0.0,
            error=raster.error,
        )


def get_text_extractor(ocr_provider_preference: str | None = None) -> TextExtractor:
    """Build a lightweight extractor around the cached configured OCR engine."""
    return TextExtractor(
        PyMuPDFEmbeddedTextProvider(),
        get_raster_ocr_provider(ocr_provider_preference),
    )


# Retained for Phase 1 imports; application code should request an extractor
# with its Settings preference rather than mutating process-global state.
DEFAULT_TEXT_EXTRACTOR = get_text_extractor()


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
        page_count = _validate_pdf(data, field)
        kind: Literal["pdf", "image"] = "pdf"
        canonical_mime = "application/pdf"
    else:
        _validate_image(data, field, extension)
        page_count = 1
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
        page_count=page_count,
    )


def _validate_pdf(data: bytes, field: str) -> int:
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
            if page_count < 1:
                raise DocumentValidationError(
                    "empty_document",
                    f"The {field} PDF has no pages.",
                    field=field,
                    details={"page_count": page_count},
                )
            if page_count > MAX_PAGES:
                raise DocumentValidationError(
                    "page_limit_exceeded",
                    f"The {field} PDF exceeds the {MAX_PAGES}-page limit.",
                    field=field,
                    details={"page_count": page_count, "max_pages": MAX_PAGES},
                )
            unusable_pages: list[int] = []
            for page_index in range(page_count):
                page = document.load_page(page_index)
                if page.rect.width <= 0 or page.rect.height <= 0:
                    raise DocumentValidationError(
                        "corrupt_pdf",
                        f"The {field} PDF page {page_index + 1} has an invalid size.",
                        field=field,
                        details={"page_number": page_index + 1},
                    )
                if not _pdf_page_has_content(page):
                    unusable_pages.append(page_index + 1)
            if unusable_pages:
                legacy_blank_multipage = page_count > 1 and len(unusable_pages) == page_count
                raise DocumentValidationError(
                    "single_page_required" if legacy_blank_multipage else "empty_page",
                    f"The {field} PDF contains an unusable empty page.",
                    field=field,
                    details={"page_count": page_count, "pages": unusable_pages},
                )
            return page_count
    except DocumentValidationError:
        raise
    except Exception as exc:
        raise DocumentValidationError(
            "corrupt_pdf", f"The {field} PDF could not be read.", field=field
        ) from exc


def _pdf_page_has_content(page: fitz.Page) -> bool:
    if (
        not page.get_text("text").strip()
        and not page.get_images(full=True)
        and not page.get_drawings()
    ):
        return False

    # Extractable text can be invisible, clipped, or white-on-white. Validate a
    # bounded rendered preview so "content" means usable visible page evidence,
    # while still avoiding full analysis rendering during upload validation.
    rect = page.rect
    scale = CONTENT_PREVIEW_MAX_DIMENSION / max(rect.width, rect.height)
    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(scale, scale),
        alpha=False,
        colorspace=fitz.csRGB,
    )
    rgb = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
        pixmap.height, pixmap.width, pixmap.n
    )
    return _raster_has_meaningful_content(rgb)


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
        with Image.open(io.BytesIO(data)) as image:
            normalized = _normalize_pil_image(image)
            normalized.thumbnail(
                (CONTENT_PREVIEW_MAX_DIMENSION, CONTENT_PREVIEW_MAX_DIMENSION),
                Image.Resampling.BILINEAR,
            )
            if not _raster_has_meaningful_content(np.asarray(normalized)):
                raise DocumentValidationError(
                    "empty_page",
                    f"The {field} image is an unusable blank page.",
                    field=field,
                    details={"page_count": 1, "pages": [1]},
                )
    except DocumentValidationError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise DocumentValidationError(
            "corrupt_image", f"The {field} image could not be decoded.", field=field
        ) from exc


def _raster_has_meaningful_content(pixels: np.ndarray) -> bool:
    """Conservatively reject uniform/near-uniform pages without OCRing them."""
    if pixels.size == 0 or pixels.ndim not in {2, 3}:
        return False
    if pixels.ndim == 2:
        pixels = pixels[:, :, np.newaxis]
    if pixels.shape[2] > 3:
        pixels = pixels[:, :, :3]

    height, width = pixels.shape[:2]
    scale = min(1.0, CONTENT_PREVIEW_MAX_DIMENSION / max(height, width))
    if scale < 1.0:
        pixels = cv2.resize(
            pixels,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
        if pixels.ndim == 2:
            pixels = pixels[:, :, np.newaxis]

    border = np.concatenate(
        (pixels[0, :, :], pixels[-1, :, :], pixels[:, 0, :], pixels[:, -1, :]),
        axis=0,
    )
    background = np.median(border.astype(np.float32), axis=0)
    contrast = np.max(
        np.abs(pixels.astype(np.float32) - background),
        axis=2,
    )
    pixel_count = contrast.size
    minimum_content_pixels = max(
        24, round(pixel_count * CONTENT_MIN_PIXEL_RATIO)
    )
    if np.count_nonzero(contrast >= CONTENT_CONTRAST_THRESHOLD) < minimum_content_pixels:
        return False

    edge_strength = np.zeros(pixels.shape[:2], dtype=np.uint16)
    for channel_index in range(pixels.shape[2]):
        channel = pixels[:, :, channel_index]
        gradient_x = cv2.Sobel(channel, cv2.CV_16S, 1, 0, ksize=3)
        gradient_y = cv2.Sobel(channel, cv2.CV_16S, 0, 1, ksize=3)
        channel_edges = np.abs(gradient_x).astype(np.uint16) + np.abs(
            gradient_y
        ).astype(np.uint16)
        edge_strength = np.maximum(edge_strength, channel_edges)
    minimum_edge_pixels = max(8, round(pixel_count * CONTENT_MIN_EDGE_RATIO))
    return bool(
        np.count_nonzero(edge_strength >= CONTENT_EDGE_THRESHOLD)
        >= minimum_edge_pixels
    )


def _normalize_pil_image(image: Image.Image) -> Image.Image:
    """Apply EXIF orientation and composite transparency onto a white page."""

    normalized = ImageOps.exif_transpose(image)
    if normalized.mode in {"RGBA", "LA"} or "transparency" in normalized.info:
        rgba = normalized.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        background.alpha_composite(rgba)
        return background.convert("RGB")
    return normalized.convert("RGB")


def save_upload(upload: ValidatedUpload, job_dir: Path, role: str) -> Path:
    input_dir = job_dir / "inputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    path = input_dir / f"{role}{upload.extension}"
    path.write_bytes(upload.data)
    return path


def render_document(upload: ValidatedUpload, max_dimension: int) -> RenderedDocument:
    """Phase 1 compatibility wrapper for the first page."""
    return render_document_page(upload, 0, max_dimension)


def render_document_page(
    upload: ValidatedUpload,
    page_index: int,
    max_dimension: int,
) -> RenderedDocument:
    _require_page_index(upload, page_index)
    if upload.kind == "pdf":
        return _render_pdf(upload.data, max_dimension, page_index)
    return _render_image(upload.data, max_dimension)


def _render_pdf(data: bytes, max_dimension: int, page_index: int = 0) -> RenderedDocument:
    try:
        with fitz.open(stream=data, filetype="pdf") as document:
            page = document.load_page(page_index)
            rect = page.rect
            scale = max_dimension / max(rect.width, rect.height)
            pixmap = page.get_pixmap(
                matrix=fitz.Matrix(scale, scale),
                alpha=False,
                colorspace=fitz.csRGB,
            )
            rgb = np.frombuffer(pixmap.samples_mv, dtype=np.uint8).reshape(
                pixmap.height, pixmap.width, 3
            )
            image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            transform = CoordinateTransform(
                original_width=max(1, round(rect.width)),
                original_height=max(1, round(rect.height)),
                normalized_width=pixmap.width,
                normalized_height=pixmap.height,
                scale_x=pixmap.width / rect.width,
                scale_y=pixmap.height / rect.height,
            )
            rendered = RenderedDocument(image=image, transform=transform)
        return rendered
    finally:
        # MuPDF's process-global resource store otherwise retains decoded raster
        # pages up to its high-water limit across independent analysis jobs.
        # The returned BGR array owns its memory, so closed-document resources
        # are safe to release without reinitializing the cached OCR provider.
        fitz.TOOLS.store_shrink(100)


def _render_image(data: bytes, max_dimension: int) -> RenderedDocument:
    with Image.open(io.BytesIO(data)) as source:
        original_width, original_height = source.size
        orientation = int(source.getexif().get(274, 1))
        orientation_degrees = {3: 180, 6: 90, 8: 270}.get(orientation, 0)
        normalized = _normalize_pil_image(source)
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


def extract_text(
    upload: ValidatedUpload,
    ocr_provider_preference: str | None = None,
) -> TextExtraction:
    """Phase 1 compatibility wrapper for the first page."""
    return get_text_extractor(ocr_provider_preference).extract(upload)


def extract_page_text(
    upload: ValidatedUpload,
    rendered: RenderedDocument,
    page_index: int = 0,
    ocr_provider_preference: str | None = None,
) -> TextExtraction:
    return get_text_extractor(ocr_provider_preference).extract(
        upload,
        page_index=page_index,
        rendered=rendered,
    )


def _require_page_index(upload: ValidatedUpload, page_index: int) -> None:
    if not 0 <= page_index < upload.page_count:
        raise IndexError(
            f"Page index {page_index} is outside the {upload.page_count}-page document"
        )


def write_png(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    success, encoded = cv2.imencode(".png", image, [cv2.IMWRITE_PNG_COMPRESSION, 6])
    if not success:
        raise RuntimeError(f"Could not encode PNG asset {path.name}")
    path.write_bytes(encoded.tobytes())
