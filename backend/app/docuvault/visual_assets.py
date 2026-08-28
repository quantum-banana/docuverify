"""Deterministic visual-reference inspection and fixed-region fingerprints.

Visual fingerprints are deliberately scoped to profile-declared fixed masks.
Variable regions are removed even when a malformed mask overlaps a fixed one,
so legitimate personal-value changes cannot improve or reduce the profile
retrieval score.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import fitz
import numpy as np


FINGERPRINT_ALGORITHM = "docuverify-visual-fingerprint-v2"
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


def compute_visual_fingerprint(
    image: np.ndarray,
    *,
    fixed_regions: Sequence[dict[str, Any]],
    variable_regions: Sequence[dict[str, Any]],
    security_regions: Mapping[str, Sequence[dict[str, Any]]],
    page_number: int,
    source_sha256: str,
) -> dict[str, Any]:
    """Return a compact, deterministic fingerprint bound to image bytes and masks.

    The fingerprint deliberately stores summaries rather than raw descriptors. It
    is retrieval evidence only; callers must never interpret it as an
    authenticity probability.
    """

    if len(source_sha256) != 64:
        raise ValueError("visual-reference source SHA-256 must contain 64 hex characters")
    try:
        bytes.fromhex(source_sha256)
    except ValueError as exc:
        raise ValueError("visual-reference source SHA-256 is not hexadecimal") from exc
    bounded = _bound_image(image)
    gray = cv2.cvtColor(bounded, cv2.COLOR_BGR2GRAY) if bounded.ndim == 3 else bounded
    fixed_mask = _region_mask(gray.shape, fixed_regions, page_number)
    variable_mask = _region_mask(gray.shape, variable_regions, page_number)
    fixed_mask[variable_mask > 0] = 0
    if not np.any(fixed_mask):
        raise ValueError("visual reference has no fixed pixels after variable masking")

    normalized = np.full(gray.shape, 255, dtype=np.uint8)
    normalized[fixed_mask > 0] = gray[fixed_mask > 0]
    edges = cv2.Canny(normalized, 60, 160)
    edge_normalized = np.full(gray.shape, 255, dtype=np.uint8)
    edge_normalized[fixed_mask > 0] = 255 - edges[fixed_mask > 0]
    layout_sample = cv2.resize(normalized, (64, 64), interpolation=cv2.INTER_AREA)
    border_width = max(2, round(min(gray.shape) * 0.025))
    border = np.concatenate(
        (
            gray[:border_width, :].reshape(-1),
            gray[-border_width:, :].reshape(-1),
            gray[:, :border_width].reshape(-1),
            gray[:, -border_width:].reshape(-1),
        )
    )

    if bounded.ndim == 2:
        colour = cv2.cvtColor(bounded, cv2.COLOR_GRAY2BGR)
    else:
        colour = bounded
    histogram: list[int] = []
    for channel in range(3):
        values = cv2.calcHist([colour], [channel], fixed_mask, [8], [0, 256]).reshape(-1)
        total = float(values.sum()) or 1.0
        histogram.extend(int(round(float(value) / total * 10000)) for value in values)

    region_hashes: dict[str, str] = {}
    height, width = gray.shape
    for index, region in enumerate(fixed_regions, start=1):
        if int(region.get("page", page_number)) != page_number:
            continue
        box = region["box"]
        x0 = max(0, min(width - 1, round(float(box["x"]) * width)))
        y0 = max(0, min(height - 1, round(float(box["y"]) * height)))
        x1 = max(x0 + 1, min(width, round((float(box["x"]) + float(box["width"])) * width)))
        y1 = max(y0 + 1, min(height, round((float(box["y"]) + float(box["height"])) * height)))
        crop = gray[y0:y1, x0:x1]
        crop_mask = fixed_mask[y0:y1, x0:x1]
        if not np.any(crop_mask):
            continue
        fixed_crop = np.full(crop.shape, 255, dtype=np.uint8)
        fixed_crop[crop_mask > 0] = crop[crop_mask > 0]
        region_id = str(region.get("region_id") or f"fixed-{index:02d}")
        region_hashes[region_id] = _phash(fixed_crop)

    geometry = {
        key: [
            {
                "page": int(region.get("page", page_number)),
                "box": region["box"],
                "region_id": region.get("region_id"),
            }
            for region in values
        ]
        for key, values in sorted(security_regions.items())
    }
    mask_sha = mask_fingerprint(fixed_regions, variable_regions, security_regions)
    return {
        "algorithm": FINGERPRINT_ALGORITHM,
        "version": "2.0.0",
        "render_max_edge": MAX_RENDER_EDGE,
        "source_sha256": source_sha256.lower(),
        "mask_sha256": mask_sha,
        "value": _phash(normalized),
        "edge_phash": _phash(edge_normalized),
        "layout_sha256": hashlib.sha256(layout_sample.tobytes()).hexdigest(),
        "border_sha256": hashlib.sha256(border.tobytes()).hexdigest(),
        "security_geometry_sha256": hashlib.sha256(_canonical_json_bytes(geometry)).hexdigest(),
        "stable_anchor_geometry_sha256": hashlib.sha256(
            _canonical_json_bytes(list(fixed_regions))
        ).hexdigest(),
        "aspect_ratio": round(float(width) / max(float(height), 1.0), 6),
        "colour_histogram": histogram,
        "fixed_region_hashes": dict(sorted(region_hashes.items())),
    }


def visual_fingerprint_matches(
    expected: Mapping[str, Any],
    image: np.ndarray,
    *,
    fixed_regions: Sequence[dict[str, Any]],
    variable_regions: Sequence[dict[str, Any]],
    security_regions: Mapping[str, Sequence[dict[str, Any]]],
    page_number: int,
    source_sha256: str,
) -> bool:
    """Recompute and compare the complete deterministic fingerprint."""

    actual = compute_visual_fingerprint(
        image,
        fixed_regions=fixed_regions,
        variable_regions=variable_regions,
        security_regions=security_regions,
        page_number=page_number,
        source_sha256=source_sha256,
    )
    return json.loads(json.dumps(expected, sort_keys=True)) == actual


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
    fixed_regions: Sequence[dict[str, Any]],
    variable_regions: Sequence[dict[str, Any]],
    security_regions: Mapping[str, Sequence[dict[str, Any]]] | None = None,
) -> str:
    """Bind precomputed fingerprints to the normalized mask definitions."""

    value = {
        "fixed": list(fixed_regions),
        "variable": list(variable_regions),
        "security": {
            key: list(values) for key, values in sorted((security_regions or {}).items())
        },
    }
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def region_mask_image(
    shape: tuple[int, int] | tuple[int, int, int],
    regions: Sequence[dict[str, Any]],
    page_number: int,
) -> np.ndarray:
    """Build a production-safe binary mask from normalized profile regions."""

    return _region_mask((int(shape[0]), int(shape[1])), regions, page_number)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _phash(gray: np.ndarray) -> str:
    if gray.size == 0:
        raise ValueError("cannot perceptually hash an empty image")
    if gray.ndim == 3:
        gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    low_frequency = cv2.dct(resized)[:8, :8]
    median = float(np.median(low_frequency[1:]))
    bits = (low_frequency > median).reshape(-1).astype(np.uint8)
    return np.packbits(bits).tobytes().hex()


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
