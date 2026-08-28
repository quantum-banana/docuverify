"""Conservative PDF/image metadata and generation-pipeline indicators."""

from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Any

import fitz
from PIL import ExifTags, Image, UnidentifiedImageError

from backend.app.models.contracts import (
    CheckStatus,
    MetadataAssessment,
    MetadataIndicator,
    Severity,
)
from backend.app.services.documents import ValidatedUpload


_EDITING_SOFTWARE = re.compile(
    r"photoshop|illustrator|gimp|canva|acrobat|libreoffice|microsoft\s+word|preview",
    re.IGNORECASE,
)


def inspect_metadata(upload: ValidatedUpload) -> MetadataAssessment:
    try:
        return _inspect_pdf(upload) if upload.kind == "pdf" else _inspect_image(upload)
    except (OSError, ValueError, fitz.FileDataError, UnidentifiedImageError):
        return MetadataAssessment(
            status=CheckStatus.UNSUPPORTED,
            explanation="Metadata could not be parsed safely; visual analysis can continue.",
            limitations=["Metadata parsing failure does not by itself indicate manipulation."],
        )


def _inspect_pdf(upload: ValidatedUpload) -> MetadataAssessment:
    indicators: list[MetadataIndicator] = []
    available: set[str] = set()
    with fitz.open(stream=upload.data, filetype="pdf") as document:
        metadata = {key: str(value).strip() for key, value in document.metadata.items() if value}
        available.update(metadata)
        creation = _pdf_date(metadata.get("creationDate"))
        modification = _pdf_date(metadata.get("modDate"))
        if creation and modification:
            available.update({"creationDate", "modDate"})
            passed = creation <= modification
            indicators.append(
                _indicator(
                    "metadata_timeline",
                    CheckStatus.PASSED if passed else CheckStatus.FAILED,
                    Severity.INFO if passed else Severity.HIGH,
                    92,
                    "Creation time precedes modification time."
                    if passed
                    else "Metadata timeline inconsistency: modification time precedes creation time.",
                    {"timeline_order_valid": passed},
                )
            )
        producer = " ".join(
            value for key, value in metadata.items() if key in {"producer", "creator"}
        )
        if producer and _EDITING_SOFTWARE.search(producer):
            indicators.append(
                _indicator(
                    "editing_software_metadata",
                    CheckStatus.WARNING,
                    Severity.LOW,
                    82,
                    "Editing or document-authoring software metadata is present; this is an indicator, not proof of improper editing.",
                    {"software_metadata_present": True},
                )
            )

        xml = document.get_xml_metadata() or ""
        if xml:
            available.add("xmp")
            xmp_created = _xmp_date(xml, "CreateDate")
            xmp_modified = _xmp_date(xml, "ModifyDate")
            conflict = bool(
                (creation and xmp_created and abs((creation - xmp_created).total_seconds()) > 300)
                or (modification and xmp_modified and abs((modification - xmp_modified).total_seconds()) > 300)
            )
            if conflict:
                indicators.append(
                    _indicator(
                        "xmp_document_info_conflict",
                        CheckStatus.WARNING,
                        Severity.MEDIUM,
                        80,
                        "XMP and PDF document-info timestamps disagree beyond the configured tolerance.",
                        {"timestamp_tolerance_seconds": 300},
                    )
                )

        revisions = max(upload.data.count(b"startxref"), upload.data.count(b"%%EOF"))
        if revisions > 1:
            indicators.append(
                _indicator(
                    "incremental_updates",
                    CheckStatus.WARNING,
                    Severity.LOW,
                    86,
                    "The PDF contains incremental revisions. Signature validation determines whether later changes are permitted.",
                    {"revision_markers": revisions},
                )
            )

        page_fonts: list[set[str]] = []
        image_filters: set[str] = set()
        for page in document:
            page_fonts.append({str(font[3]) for font in page.get_fonts(full=True) if len(font) > 3})
            for image in page.get_images(full=True):
                xref = int(image[0])
                object_text = document.xref_object(xref, compressed=False)
                image_filters.update(re.findall(r"/Filter\s*/([A-Za-z0-9]+)", object_text))
        if len(page_fonts) > 1 and any(page_fonts[0] != fonts for fonts in page_fonts[1:]):
            indicators.append(
                _indicator(
                    "mixed_generation_pipeline",
                    CheckStatus.WARNING,
                    Severity.LOW,
                    62,
                    "Embedded-font sets differ between pages, consistent with a mixed generation pipeline or legitimate appended content.",
                    {"distinct_page_font_sets": len({tuple(sorted(fonts)) for fonts in page_fonts})},
                )
            )
        if len(image_filters) > 1:
            indicators.append(
                _indicator(
                    "mixed_compression",
                    CheckStatus.WARNING,
                    Severity.LOW,
                    58,
                    "Multiple embedded-image compression filters were observed; this can be legitimate or reflect mixed source material.",
                    {"compression_filter_count": len(image_filters)},
                )
            )

    return _assessment(indicators, available)


def _inspect_image(upload: ValidatedUpload) -> MetadataAssessment:
    indicators: list[MetadataIndicator] = []
    available: set[str] = set()
    with Image.open(io.BytesIO(upload.data)) as image:
        exif = image.getexif()
        values: dict[str, Any] = {}
        for key, value in exif.items():
            name = str(ExifTags.TAGS.get(key, key))
            values[name] = value
            available.add(name)
        software = str(values.get("Software", ""))
        if software and _EDITING_SOFTWARE.search(software):
            indicators.append(
                _indicator(
                    "editing_software_metadata",
                    CheckStatus.WARNING,
                    Severity.LOW,
                    82,
                    "Image software metadata is present; it does not identify who edited the file or why.",
                    {"software_metadata_present": True},
                )
            )
        created = _exif_date(values.get("DateTimeOriginal"))
        modified = _exif_date(values.get("DateTime"))
        if created and modified:
            passed = created <= modified
            indicators.append(
                _indicator(
                    "metadata_timeline",
                    CheckStatus.PASSED if passed else CheckStatus.FAILED,
                    Severity.INFO if passed else Severity.HIGH,
                    90,
                    "EXIF time order is consistent."
                    if passed
                    else "Metadata timeline inconsistency: the modification timestamp predates image capture metadata.",
                    {"timeline_order_valid": passed},
                )
            )
        if not exif:
            indicators.append(
                _indicator(
                    "metadata_unavailable",
                    CheckStatus.NOT_APPLICABLE,
                    Severity.INFO,
                    95,
                    "No EXIF metadata was available. Absence alone is not suspicious.",
                    {},
                )
            )
    return _assessment(indicators, available)


def _assessment(indicators: list[MetadataIndicator], available: set[str]) -> MetadataAssessment:
    if any(item.status is CheckStatus.FAILED for item in indicators):
        status = CheckStatus.FAILED
        explanation = "A metadata timeline or internal metadata consistency check failed."
    elif any(item.status is CheckStatus.WARNING for item in indicators):
        status = CheckStatus.WARNING
        explanation = "Metadata indicators warrant context-aware review but do not identify an editor or prove tampering."
    elif any(item.status is CheckStatus.PASSED for item in indicators):
        status = CheckStatus.PASSED
        explanation = "Available metadata checks were internally consistent."
    else:
        status = CheckStatus.NOT_APPLICABLE
        explanation = "Metadata was unavailable or too limited for a reliable inference."
    return MetadataAssessment(
        status=status,
        indicators=indicators,
        available_fields=sorted(available),
        explanation=explanation,
        limitations=[
            "Metadata can be removed, rewritten or absent during legitimate processing.",
            "Source tracing cannot identify a downloader, website or human editor without direct evidence.",
        ],
    )


def _indicator(
    category: str,
    status: CheckStatus,
    severity: Severity,
    confidence: float,
    explanation: str,
    measurements: dict[str, str | int | float | bool | None],
) -> MetadataIndicator:
    return MetadataIndicator(
        category=category,
        status=status,
        severity=severity,
        confidence_score=confidence,
        explanation=explanation,
        supporting_measurements=measurements,
    )


def _pdf_date(value: str | None) -> datetime | None:
    if not value:
        return None
    match = re.search(r"(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?", value)
    if not match:
        return None
    parts = [int(item) if item else default for item, default in zip(match.groups(), [0, 1, 1, 0, 0, 0], strict=True)]
    try:
        return datetime(*parts)
    except ValueError:
        return None


def _xmp_date(xml: str, field: str) -> datetime | None:
    match = re.search(rf"(?:xmp:)?{re.escape(field)}[=\">\s]+([^<\"]+)", xml, re.IGNORECASE)
    if not match:
        return None
    try:
        return datetime.fromisoformat(match.group(1).strip().replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _exif_date(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
