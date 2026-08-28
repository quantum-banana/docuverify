"""Offline QR detection/decoding with explicit cryptographic boundaries."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Protocol, Sequence
from urllib.parse import parse_qs
from xml.etree import ElementTree

import cv2
import numpy as np

from backend.app.docuvault.repository import DocumentProfile
from backend.app.models.contracts import (
    BoundingBox,
    CheckStatus,
    CodeAssessment,
    CodeCheckResult,
)
from backend.app.services.logical_rules import ExtractedField


@dataclass(frozen=True, slots=True)
class DecodedCode:
    symbology: str
    payload: str
    points: np.ndarray
    decoder: str


class BarcodeProvider(Protocol):
    name: str
    supported_symbologies: tuple[str, ...]

    def detect_and_decode(self, image: np.ndarray) -> tuple[DecodedCode, ...]: ...


class OpenCVQRCodeProvider:
    name = "opencv_qrcode_detector"
    supported_symbologies = ("QR",)

    def __init__(self) -> None:
        self._detector = cv2.QRCodeDetector()

    def detect_and_decode(self, image: np.ndarray) -> tuple[DecodedCode, ...]:
        results: list[DecodedCode] = []
        try:
            detected, decoded, points, _ = self._detector.detectAndDecodeMulti(image)
            if points is not None and len(points):
                payloads = list(decoded) if detected else [""] * len(points)
                payloads.extend([""] * (len(points) - len(payloads)))
                results.extend(
                    DecodedCode("QR", str(payloads[index]), np.asarray(polygon, dtype=np.float32), self.name)
                    for index, polygon in enumerate(points)
                )
        except (cv2.error, ValueError):
            pass
        if not results:
            try:
                payload, points, _ = self._detector.detectAndDecode(image)
                if points is not None and np.asarray(points).size:
                    results.append(
                        DecodedCode("QR", str(payload), np.asarray(points, dtype=np.float32).reshape(-1, 2), self.name)
                    )
            except cv2.error:
                pass
        if not results:
            try:
                detected, points = self._detector.detect(image)
                if detected and points is not None:
                    results.append(
                        DecodedCode("QR", "", np.asarray(points, dtype=np.float32).reshape(-1, 2), self.name)
                    )
            except cv2.error:
                pass
        return tuple(results)


class UnsupportedBarcodeProvider:
    name = "unsupported_additional_barcode_provider"
    supported_symbologies: tuple[str, ...] = ()

    def detect_and_decode(self, image: np.ndarray) -> tuple[DecodedCode, ...]:
        return ()


def analyze_codes(
    pages: Sequence[Any],
    profile: DocumentProfile | None,
    visible_fields: dict[str, ExtractedField] | None = None,
    *,
    providers: Sequence[BarcodeProvider] | None = None,
) -> tuple[CodeAssessment, dict[str, str]]:
    configured = tuple(providers or (OpenCVQRCodeProvider(), UnsupportedBarcodeProvider()))
    code_settings = profile.manifest["codes"] if profile else None
    expectation = str(code_settings["qr_expectation"]) if code_settings else "unknown"
    results: list[CodeCheckResult] = []
    decoded_fields: dict[str, str] = {}
    for page_number, page in enumerate(pages, start=1):
        image = cv2.imread(str(page.image_path), cv2.IMREAD_COLOR)
        if image is None:
            continue
        for provider in configured:
            for code in provider.detect_and_decode(image):
                box = _normalised_box(code.points, image.shape[1], image.shape[0])
                parsed, payload_format = _parse_payload(code.payload)
                if parsed and not decoded_fields:
                    decoded_fields = parsed
                structure_valid = _structure_valid(code.payload, parsed, code_settings)
                consistent = _visible_consistency(parsed, visible_fields or {})
                indicators = _structural_indicators(
                    image,
                    code.points,
                    box,
                    profile,
                    page_number,
                )
                decoded = bool(code.payload)
                result_status = CheckStatus.PASSED
                if decoded and (structure_valid is False or consistent is False):
                    result_status = CheckStatus.FAILED
                elif indicators:
                    result_status = CheckStatus.WARNING
                payload_summary = None
                digest = None
                if decoded:
                    digest = hashlib.sha256(code.payload.encode("utf-8", errors="replace")).hexdigest()
                    if parsed:
                        payload_summary = (
                            f"Decoded {payload_format} payload with fields: "
                            + ", ".join(sorted(parsed)[:20])
                            + ". Values are redacted."
                        )
                    else:
                        payload_summary = "Decoded an unstructured payload; the value is redacted."
                crypto_available = bool(
                    code_settings and code_settings.get("cryptographic_specification")
                )
                explanation = (
                    "Code detected and decoded locally. Payload decoding is not cryptographic authenticity."
                    if decoded
                    else "Code geometry was detected, but the local decoder could not recover a payload."
                )
                if not crypto_available:
                    explanation += " No authoritative local cryptographic verifier/certificate is configured for this issuer format."
                results.append(
                    CodeCheckResult(
                        code_index=len(results) + 1,
                        page_number=page_number,
                        symbology=code.symbology,
                        bounding_box=box,
                        detected=True,
                        decoded=decoded,
                        decoder=code.decoder,
                        confidence_score=95.0 if decoded else 62.0,
                        payload_summary=payload_summary,
                        payload_sha256=digest,
                        structure_valid=structure_valid,
                        visible_fields_consistent=consistent,
                        cryptographic_verification_available=crypto_available,
                        cryptographic_verification_result=(
                            CheckStatus.SKIPPED if crypto_available else CheckStatus.UNSUPPORTED
                        ),
                        structural_tampering_indicators=indicators,
                        explanation=explanation,
                    )
                )
    detected_count = len(results)
    decoded_count = sum(result.decoded for result in results)
    if not results:
        if expectation == "required":
            status = CheckStatus.FAILED
            explanation = "The selected profile requires a QR code, but none was detected."
        elif expectation == "optional":
            status = CheckStatus.NOT_APPLICABLE
            explanation = "No QR code was detected; the selected profile marks it as optional."
        else:
            status = CheckStatus.NOT_APPLICABLE
            explanation = "No supported QR/barcode was detected; visual analysis continued."
    elif any(
        result.structure_valid is False or result.visible_fields_consistent is False
        for result in results
    ):
        status = CheckStatus.FAILED
        explanation = "A decoded QR payload disagrees with its expected structure or reliably extracted visible fields."
    elif any(result.structural_tampering_indicators for result in results):
        status = CheckStatus.WARNING
        explanation = "QR content decoded, but placement or local geometry warrants review."
    elif decoded_count:
        status = CheckStatus.PASSED
        explanation = "QR payload(s) decoded and available visible fields were consistent; cryptographic trust remains separate."
    else:
        status = CheckStatus.WARNING
        explanation = "QR geometry was detected but no payload could be decoded."
    return (
        CodeAssessment(
            status=status,
            expected=expectation,
            detected_count=detected_count,
            decoded_count=decoded_count,
            results=results,
            explanation=explanation,
        ),
        decoded_fields,
    )


def _parse_payload(payload: str) -> tuple[dict[str, str], str]:
    if not payload:
        return {}, "unknown"
    if len(payload.encode("utf-8", errors="replace")) > 64 * 1024:
        return {}, "oversized"
    try:
        value = json.loads(payload)
        if isinstance(value, dict):
            return {_normalise_key(str(key)): str(item)[:500] for key, item in value.items()}, "JSON"
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    if "=" in payload:
        query = parse_qs(payload.split("?", 1)[-1], keep_blank_values=True, max_num_fields=100)
        if query:
            return {_normalise_key(key): str(values[0])[:500] for key, values in query.items()}, "query"
    if payload.lstrip().startswith("<"):
        try:
            root = ElementTree.fromstring(payload)
            fields = {
                _normalise_key(element.tag.split("}")[-1]): (element.text or "")[:500]
                for element in root.iter()
                if len(element) == 0 and element.text
            }
            if fields:
                return fields, "XML"
        except ElementTree.ParseError:
            pass
    pairs = re.findall(r"([A-Za-z][A-Za-z0-9 _-]{1,40})\s*[:=]\s*([^|;\n]{1,500})", payload)
    if pairs:
        return {_normalise_key(key): value.strip() for key, value in pairs}, "text"
    return {}, "text"


def _normalise_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")[:80]


def _structure_valid(
    payload: str,
    fields: dict[str, str],
    settings: dict[str, Any] | None,
) -> bool | None:
    if not payload:
        return None
    if not settings:
        return None
    required = {_normalise_key(str(key)) for key in settings.get("required_keys", [])}
    if required and not required.issubset(fields):
        return False
    prefixes = [str(prefix).casefold() for prefix in settings.get("issuer_prefixes", [])]
    if prefixes and not fields and not any(payload.casefold().startswith(prefix) for prefix in prefixes):
        return False
    return True


def _visible_consistency(
    payload_fields: dict[str, str],
    visible_fields: dict[str, ExtractedField],
) -> bool | None:
    comparisons: list[bool] = []
    aliases = {
        "document_number": {"document_number", "registration_number", "id", "identifier", "number"},
        "name": {"name", "student_name", "candidate_name", "holder_name"},
        "issue_date": {"issue_date", "date_of_issue", "issued_on"},
    }
    for field_id, visible in visible_fields.items():
        possible = aliases.get(field_id, {field_id})
        qr_value = next((payload_fields[key] for key in possible if key in payload_fields), None)
        if qr_value is None:
            continue
        comparisons.append(_normalise_value(qr_value) == _normalise_value(visible.value))
    return all(comparisons) if comparisons else None


def _normalise_value(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _normalised_box(points: np.ndarray, width: int, height: int) -> BoundingBox:
    polygon = np.asarray(points, dtype=np.float32).reshape(-1, 2)
    x0, y0 = np.min(polygon, axis=0)
    x1, y1 = np.max(polygon, axis=0)
    nx, ny = max(0.0, float(x0 / width)), max(0.0, float(y0 / height))
    nw = max(1 / width, min(1.0 - nx, float((x1 - x0) / width)))
    nh = max(1 / height, min(1.0 - ny, float((y1 - y0) / height)))
    return BoundingBox(x=nx, y=ny, width=nw, height=nh)


def _structural_indicators(
    image: np.ndarray,
    points: np.ndarray,
    box: BoundingBox,
    profile: DocumentProfile | None,
    page_number: int,
) -> list[str]:
    indicators: list[str] = []
    polygon = np.asarray(points, dtype=np.float32).reshape(-1, 2)
    if len(polygon) >= 4:
        sides = [float(np.linalg.norm(polygon[(index + 1) % 4] - polygon[index])) for index in range(4)]
        if min(sides) > 0 and max(sides) / min(sides) > 2.1:
            indicators.append("QR quadrilateral has unusually distorted geometry")
        angles = []
        for index in range(4):
            first = polygon[index - 1] - polygon[index]
            second = polygon[(index + 1) % 4] - polygon[index]
            cosine = float(np.dot(first, second) / max(np.linalg.norm(first) * np.linalg.norm(second), 1e-6))
            angles.append(math.degrees(math.acos(max(-1.0, min(1.0, cosine)))))
        if max(abs(angle - 90.0) for angle in angles) > 28.0:
            indicators.append("QR perspective geometry is outside the conservative tolerance")
    if profile is not None:
        expected = [
            region
            for region in profile.manifest["security_regions"]["qr"]
            if int(region["page"]) == page_number
        ]
        if expected and max((_box_iou(box, region["box"]) for region in expected), default=0.0) < 0.04:
            indicators.append("QR code is displaced from the profile-defined region")
    height, width = image.shape[:2]
    x0, y0 = int(box.x * width), int(box.y * height)
    x1, y1 = min(width, int((box.x + box.width) * width)), min(height, int((box.y + box.height) * height))
    margin = max(3, round(min(x1 - x0, y1 - y0) * 0.08))
    outer = image[max(0, y0 - margin) : min(height, y1 + margin), max(0, x0 - margin) : min(width, x1 + margin)]
    inner = image[y0:y1, x0:x1]
    if outer.size and inner.size:
        outer_noise = float(cv2.Laplacian(cv2.cvtColor(outer, cv2.COLOR_BGR2GRAY), cv2.CV_32F).var())
        inner_noise = float(cv2.Laplacian(cv2.cvtColor(inner, cv2.COLOR_BGR2GRAY), cv2.CV_32F).var())
        ratio = max(outer_noise, inner_noise) / max(min(outer_noise, inner_noise), 1.0)
        if ratio > 8.0:
            indicators.append("QR region has a strong local sharpness/compositing discontinuity")
    return indicators


def _box_iou(first: BoundingBox, second: dict[str, float]) -> float:
    ax0, ay0, ax1, ay1 = first.x, first.y, first.x + first.width, first.y + first.height
    bx0, by0 = float(second["x"]), float(second["y"])
    bx1, by1 = bx0 + float(second["width"]), by0 + float(second["height"])
    overlap = max(0.0, min(ax1, bx1) - max(ax0, bx0)) * max(0.0, min(ay1, by1) - max(ay0, by0))
    union = first.width * first.height + float(second["width"]) * float(second["height"]) - overlap
    return overlap / max(union, 1e-9)
