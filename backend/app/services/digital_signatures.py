"""Local PDF signature validation with an explicit, offline trust store."""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from typing import Any

from pyhanko.keys import load_certs_from_pemder
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.validation import validate_pdf_signature
from pyhanko_certvalidator import ValidationContext

from backend.app.models.contracts import (
    CertificateSummary,
    DigitalSignatureAssessment,
    PdfSignatureCheck,
    PdfSignatureStatus,
)
from backend.app.services.documents import ValidatedUpload


_CERTIFICATE_SUFFIXES = {".pem", ".der", ".cer", ".crt"}
_MAX_CERTIFICATE_BYTES = 2 * 1024 * 1024


def inspect_pdf_signatures(
    upload: ValidatedUpload,
    trust_store: Path,
) -> DigitalSignatureAssessment:
    if upload.kind != "pdf":
        return DigitalSignatureAssessment(
            status=PdfSignatureStatus.UNSIGNED,
            explanation="The uploaded image format cannot contain an embedded PDF signature.",
            limitations=["Image signatures or visible signature graphics are assessed separately."],
        )
    roots, trust_error = _load_trust_roots(trust_store)
    try:
        reader = PdfFileReader(io.BytesIO(upload.data), strict=False)
        signatures = list(reader.embedded_signatures)
    except Exception as exc:  # pyHanko exposes several parser-specific error types
        if b"/ByteRange" in upload.data or b"/Type/Sig" in upload.data.replace(b" ", b""):
            return DigitalSignatureAssessment(
                status=PdfSignatureStatus.UNSUPPORTED,
                explanation="A signature object was present but could not be parsed safely.",
                limitations=[f"Parser category: {type(exc).__name__}; no trust claim was made."],
            )
        return DigitalSignatureAssessment(
            status=PdfSignatureStatus.UNSIGNED,
            explanation="No parseable embedded PDF signature was found.",
        )
    if not signatures:
        return DigitalSignatureAssessment(
            status=PdfSignatureStatus.UNSIGNED,
            explanation="No embedded PDF signature was found.",
            limitations=[
                "A visible handwritten signature or seal is not a cryptographic PDF signature."
            ],
        )

    checks: list[PdfSignatureCheck] = []
    for index, embedded in enumerate(signatures, start=1):
        checks.append(
            _validate_one(
                embedded,
                index=index,
                total_revisions=int(getattr(reader, "total_revisions", 1)),
                roots=roots,
                trust_error=trust_error,
            )
        )
    overall = _overall_status(checks)
    trusted = sum(check.status is PdfSignatureStatus.VALID_TRUSTED for check in checks)
    unknown = sum(check.status is PdfSignatureStatus.VALID_UNKNOWN_TRUST for check in checks)
    explanation = (
        f"Inspected {len(checks)} embedded signature(s): {trusted} locally trusted, "
        f"{unknown} cryptographically valid with unknown signer trust."
    )
    if overall is PdfSignatureStatus.MODIFIED:
        explanation = "At least one signature indicates a disallowed or suspicious update after signing."
    elif overall is PdfSignatureStatus.INVALID:
        explanation = "At least one embedded signature failed cryptographic integrity validation."
    elif overall is PdfSignatureStatus.UNSUPPORTED:
        explanation = "At least one embedded signature format could not be validated safely."
    limitations = [
        "Certificate trust is evaluated only against the explicit local DocuVerify trust store.",
        "A valid unknown signer is not treated as an official issuer.",
    ]
    if trust_error:
        limitations.append(trust_error)
    return DigitalSignatureAssessment(
        status=overall,
        signature_count=len(checks),
        checks=checks,
        explanation=explanation,
        limitations=limitations,
    )


def _load_trust_roots(trust_store: Path) -> tuple[list[Any], str | None]:
    try:
        root = trust_store.resolve()
        if not root.exists():
            return [], "The explicit local trust-store directory does not exist; signer trust remains unknown."
        if not root.is_dir():
            return [], "The configured trust-store path is not a directory; signer trust remains unknown."
        files: list[Path] = []
        for path in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
            if path.is_symlink() or not path.is_file() or path.suffix.casefold() not in _CERTIFICATE_SUFFIXES:
                continue
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
            if resolved.stat().st_size > _MAX_CERTIFICATE_BYTES:
                continue
            files.append(resolved)
        if not files:
            return [], None
        return list(load_certs_from_pemder(files)), None
    except Exception as exc:
        return [], f"Trust-store certificates could not be loaded ({type(exc).__name__}); signer trust remains unknown."


def _validate_one(
    embedded: Any,
    *,
    index: int,
    total_revisions: int,
    roots: list[Any],
    trust_error: str | None,
) -> PdfSignatureCheck:
    signed_revision = int(getattr(embedded, "signed_revision", max(0, total_revisions - 1)))
    later_revisions = max(0, total_revisions - signed_revision - 1)
    try:
        context = ValidationContext(
            trust_roots=roots,
            other_certs=[],
            allow_fetching=False,
            revocation_mode="soft-fail",
        )
        status = validate_pdf_signature(embedded, context)
        intact = bool(status.intact)
        valid = bool(status.valid)
        trusted = bool(status.trusted)
        modification_name = str(getattr(getattr(status, "modification_level", None), "name", "NONE"))
        suspicious_update = bool(
            getattr(status, "docmdp_ok", None) is False
            or modification_name in {"FORM_FILLING", "ANNOTATIONS", "OTHER"}
        )
        if not intact or not valid:
            result_status = PdfSignatureStatus.INVALID
            explanation = "The signature's byte-range digest or cryptographic signature did not validate."
        elif suspicious_update:
            result_status = PdfSignatureStatus.MODIFIED
            explanation = "Signed content was followed by an update that is not allowed by the signature policy."
        elif trusted:
            result_status = PdfSignatureStatus.VALID_TRUSTED
            explanation = "The signature is cryptographically intact and chains to an explicit local trust root."
        else:
            result_status = PdfSignatureStatus.VALID_UNKNOWN_TRUST
            explanation = "The signature is cryptographically intact, but its signer does not chain to an explicit local trust root."
        if trust_error and result_status is PdfSignatureStatus.VALID_UNKNOWN_TRUST:
            explanation += " The configured trust store could not be loaded."
        return PdfSignatureCheck(
            signature_index=index,
            field_name=str(getattr(embedded, "field_name", "")) or None,
            status=result_status,
            cryptographically_intact=intact and valid,
            signer_locally_trusted=trusted,
            signed_content_modified=suspicious_update,
            incremental_updates=later_revisions,
            signing_time=_normalise_datetime(getattr(status, "signer_reported_dt", None)),
            certificate=_certificate_summary(getattr(status, "signing_cert", None)),
            explanation=explanation,
        )
    except Exception as exc:
        return PdfSignatureCheck(
            signature_index=index,
            field_name=str(getattr(embedded, "field_name", "")) or None,
            status=PdfSignatureStatus.UNSUPPORTED,
            incremental_updates=later_revisions,
            certificate=_certificate_summary(getattr(embedded, "signer_cert", None)),
            explanation=f"The signature could not be validated safely ({type(exc).__name__}); no trust claim was made.",
        )


def _certificate_summary(certificate: Any) -> CertificateSummary | None:
    if certificate is None:
        return None
    try:
        validity = certificate["tbs_certificate"]["validity"]
        return CertificateSummary(
            subject=str(certificate.subject.human_friendly)[:1000],
            issuer=str(certificate.issuer.human_friendly)[:1000],
            serial_number=str(certificate.serial_number),
            valid_from=_normalise_datetime(validity["not_before"].native),
            valid_to=_normalise_datetime(validity["not_after"].native),
        )
    except Exception:
        return CertificateSummary()


def _normalise_datetime(value: Any) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _overall_status(checks: list[PdfSignatureCheck]) -> PdfSignatureStatus:
    priorities = (
        PdfSignatureStatus.MODIFIED,
        PdfSignatureStatus.INVALID,
        PdfSignatureStatus.UNSUPPORTED,
        PdfSignatureStatus.VALID_UNKNOWN_TRUST,
        PdfSignatureStatus.VALID_TRUSTED,
    )
    statuses = {check.status for check in checks}
    return next(status for status in priorities if status in statuses)
