"""Deterministic visual-reference inspection and fixed-region fingerprints.

Visual fingerprints are deliberately scoped to profile-declared fixed masks.
Variable regions are removed even when a malformed mask overlaps a fixed one,
so legitimate personal-value changes cannot improve or reduce the profile
retrieval score.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import cv2
import fitz
import numpy as np


FINGERPRINT_ALGORITHM = "phash-64-fixed-v1"
FINGERPRINT_HEX_LENGTH = 16
MAX_RENDER_EDGE = 1200

_MIME_SUFFIXES = {
    "application/pdf": {".pdf"},
    "image/png": {".png"},
    "image/jpeg": {".jpg", ".jpeg"},
}


@dataclass(frozen=True, slots=True)
class VisualDimensions:
    width: float
    height: float
    unit: str


def verify_visual_media(path: Path, mime_type: str) -> None:
    """Reject a declared MIME type that does not match suffix and magic bytes."""

    suffixes = _MIME_SUFFIXES.get(mime_type)
    if suffixes is None:
        raise ValueError(f"unsupported visual-reference MIME type: {mime_type}")
    if path.suffix.casefold() not in suffixes:
        raise ValueError("visual-reference suffix does not match its MIME type")
    with path.open("rb") as source:
        header = source.read(16)
    valid_magic = (
        mime_type == "application/pdf" and header.startswith(b"%PDF-")
    ) or (
        mime_type == "image/png" and header.startswith(b"\x89PNG\r\n\x1a\n")
    ) or (
        mime_type == "image/jpeg" and header.startswith(b"\xff\xd8\xff")
    )
    if not valid_magic:
        raise ValueError("visual-reference bytes do not match the declared MIME type")


def visual_dimensions(path: Path, mime_type: str, page_number: int) -> VisualDimensions:
    """Return source dimensions without exposing or persisting document text."""

    verify_visual_media(path, mime_type)
    if page_number < 1:
        raise ValueError("visual-reference page numbers start at one")
    if mime_type == "application/pdf":
        try:
            with fitz.open(path) as document:
                if page_number > document.page_count:
                    raise ValueError("visual-reference page is outside the source document")
                rectangle = document.load_page(page_number - 1).rect
                return VisualDimensions(float(rectangle.width), float(rectangle.height), "points")
        except (fitz.FileDataError, RuntimeError) as exc:
            raise ValueError("visual-reference PDF could not be inspected") from exc
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("visual-reference image could not be decoded")
    height, width = image.shape[:2]
    if page_number != 1:
        raise ValueError("raster visual references contain exactly one page")
    return VisualDimensions(float(width), float(height), "pixels")


def render_visual_page(path: Path, mime_type: str, page_number: int) -> np.ndarray:
    """Render a visual reference into a bounded, normalized BGR image."""

    dimensions = visual_dimensions(path, mime_type, page_number)
    if mime_type != "application/pdf":
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:  # pragma: no cover - guarded by visual_dimensions
            raise ValueError("visual-reference image could not be decoded")
        return _bound_image(image)
    scale = min(MAX_RENDER_EDGE / max(dimensions.width, dimensions.height), 3.0)
    try:
        with fitz.open(path) as document:
            page = document.load_page(page_number - 1)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            rgb = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height, pixmap.width, pixmap.n
            )
            if pixmap.n == 4:
                return cv2.cvtColor(rgb, cv2.COLOR_RGBA2BGR)
            return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    except (fitz.FileDataError, RuntimeError) as exc:
        raise ValueError("visual-reference PDF could not be rendered") from exc


def fixed_region_fingerprint(
    image: np.ndarray,
    *,
    fixed_regions: Sequence[dict[str, Any]],
    variable_regions: Sequence[dict[str, Any]],
    page_number: int,
) -> str:
    """Compute a 64-bit pHash over fixed regions only."""

    if image.size == 0:
        raise ValueError("cannot fingerprint an empty visual reference")
    bounded = _bound_image(image)
    gray = cv2.cvtColor(bounded, cv2.COLOR_BGR2GRAY) if bounded.ndim == 3 else bounded
    mask = _region_mask(gray.shape, fixed_regions, page_number)
    if not np.any(mask):
        raise ValueError("a visual reference requires a non-empty fixed-region mask")
    variable_mask = _region_mask(gray.shape, variable_regions, page_number)
    mask[variable_mask > 0] = 0
    if not np.any(mask):
        raise ValueError("variable masks cannot cover every fixed-reference pixel")
    normalized = np.full(gray.shape, 255, dtype=np.uint8)
    normalized[mask > 0] = gray[mask > 0]
    resized = cv2.resize(normalized, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    low_frequency = cv2.dct(resized)[:8, :8]
    median = float(np.median(low_frequency[1:]))
    bits = (low_frequency > median).reshape(-1).astype(np.uint8)
    return np.packbits(bits).tobytes().hex()


def fingerprint_similarity(expected: str, actual: str) -> float:
    if len(expected) != FINGERPRINT_HEX_LENGTH or len(actual) != FINGERPRINT_HEX_LENGTH:
        raise ValueError("invalid fixed-region fingerprint length")
    try:
        left = np.unpackbits(np.frombuffer(bytes.fromhex(expected), dtype=np.uint8))
        right = np.unpackbits(np.frombuffer(bytes.fromhex(actual), dtype=np.uint8))
    except ValueError as exc:
        raise ValueError("fixed-region fingerprint is not hexadecimal") from exc
    return float(1.0 - np.count_nonzero(left != right) / left.size)


def mask_fingerprint(
    fixed_regions: Sequence[dict[str, Any]], variable_regions: Sequence[dict[str, Any]]
) -> str:
    """Bind precomputed fingerprints to the normalized mask definitions."""

    import json

    value = {"fixed": list(fixed_regions), "variable": list(variable_regions)}
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _bound_image(image: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    longest = max(height, width)
    if longest <= MAX_RENDER_EDGE:
        return image
    scale = MAX_RENDER_EDGE / longest
    return cv2.resize(
        image,
        (max(1, round(width * scale)), max(1, round(height * scale))),
        interpolation=cv2.INTER_AREA,
    )


def _region_mask(
    shape: tuple[int, int], regions: Sequence[dict[str, Any]], page_number: int
) -> np.ndarray:
    height, width = shape
    mask = np.zeros((height, width), dtype=np.uint8)
    for region in regions:
        if int(region.get("page", page_number)) != page_number:
            continue
        box = region["box"]
        x0 = max(0, min(width, round(float(box["x"]) * width)))
        y0 = max(0, min(height, round(float(box["y"]) * height)))
        x1 = max(x0, min(width, round((float(box["x"]) + float(box["width"])) * width)))
        y1 = max(y0, min(height, round((float(box["y"]) + float(box["height"])) * height)))
        mask[y0:y1, x0:x1] = 255
    return mask
