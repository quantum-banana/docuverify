"""Bounded local metadata extraction for exact document comparison."""

from __future__ import annotations

import io
from dataclasses import dataclass

import fitz
from PIL import ExifTags, Image

from backend.app.services.documents import ValidatedUpload


_PDF_FIELDS = (
    "title",
    "author",
    "subject",
    "keywords",
    "creator",
    "producer",
    "creationDate",
    "modDate",
    "trapped",
)
_MAX_VALUE_CHARACTERS = 256


@dataclass(frozen=True, slots=True)
class MetadataChange:
    field: str
    reference_value: str
    candidate_value: str


def extract_document_metadata(upload: ValidatedUpload) -> dict[str, str]:
    """Return safe scalar metadata; malformed optional metadata is ignored."""
    try:
        if upload.kind == "pdf":
            with fitz.open(stream=upload.data, filetype="pdf") as document:
                raw = document.metadata or {}
            return {
                field: _bounded(raw.get(field))
                for field in _PDF_FIELDS
                if _bounded(raw.get(field))
            }

        with Image.open(io.BytesIO(upload.data)) as image:
            exif = image.getexif()
            return {
                f"exif:{ExifTags.TAGS.get(tag, str(tag))}": value
                for tag, raw_value in sorted(exif.items(), key=lambda item: item[0])
                if (value := _bounded(raw_value))
            }
    except Exception:
        # Metadata is optional evidence. Validation/rendering still owns file
        # integrity, and a metadata parser failure must not stop visual analysis.
        return {}


def compare_document_metadata(
    reference: ValidatedUpload,
    candidate: ValidatedUpload,
) -> tuple[MetadataChange, ...]:
    reference_metadata = extract_document_metadata(reference)
    candidate_metadata = extract_document_metadata(candidate)
    return tuple(
        MetadataChange(
            field=field,
            reference_value=reference_metadata.get(field, ""),
            candidate_value=candidate_metadata.get(field, ""),
        )
        for field in sorted(reference_metadata.keys() | candidate_metadata.keys())
        if reference_metadata.get(field, "") != candidate_metadata.get(field, "")
    )


def _bounded(value: object) -> str:
    if value is None or isinstance(value, (bytes, bytearray, memoryview)):
        return ""
    normalized = " ".join(str(value).replace("\x00", "").split())
    return normalized[:_MAX_VALUE_CHARACTERS]
