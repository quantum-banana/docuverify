from __future__ import annotations

import copy
import io
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import cv2
import fitz
import numpy as np
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import fields, signers

from backend.app.docuvault.repository import DocumentProfile, ProfileRepository
from backend.app.models.contracts import CheckStatus, PdfSignatureStatus
from backend.app.services.digital_signatures import inspect_pdf_signatures
from backend.app.services.documents import TextExtraction, ValidatedUpload, validate_upload
from backend.app.services.logical_rules import ExtractedField, evaluate_logical_rules
from backend.app.services.metadata_forensics import inspect_metadata
from backend.app.services.qr_codes import analyze_codes


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _profile_repository(tmp_path: Path) -> ProfileRepository:
    repository = ProfileRepository(
        bundled_root=PROJECT_ROOT / "backend" / "docuvault" / "profiles",
        schema_path=PROJECT_ROOT / "backend" / "docuvault" / "schemas" / "profile.v1.schema.json",
        index_path=tmp_path / "profiles.sqlite3",
        project_root=PROJECT_ROOT,
    )
    repository.startup()
    return repository


def _pdf_upload(data: bytes, filename: str = "fixture.pdf") -> ValidatedUpload:
    return validate_upload(
        field="candidate",
        filename=filename,
        content_type="application/pdf",
        data=data,
        max_bytes=max(1, len(data)),
    )


def _unsigned_pdf(metadata: dict[str, str] | None = None) -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "FICTIONAL SIGNATURE TEST DOCUMENT")
    if metadata:
        document.set_metadata(metadata)
    data = document.tobytes()
    document.close()
    return data


def _ephemeral_signer() -> tuple[signers.SimpleSigner, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "DocuVerify Fictional Test Signer")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=True,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    password = b"fictional-ephemeral-test"
    pfx = pkcs12.serialize_key_and_certificates(
        b"docuverify-test",
        key,
        certificate,
        None,
        serialization.BestAvailableEncryption(password),
    )
    signer = signers.SimpleSigner.load_pkcs12_data(pfx, other_certs=[], passphrase=password)
    certificate_pem = certificate.public_bytes(serialization.Encoding.PEM)
    return signer, certificate_pem


def _signed_pdf() -> tuple[bytes, bytes]:
    signer, certificate_pem = _ephemeral_signer()
    writer = IncrementalPdfFileWriter(io.BytesIO(_unsigned_pdf()))
    output = signers.sign_pdf(
        writer,
        signature_meta=signers.PdfSignatureMetadata(field_name="FictionalSignature"),
        signer=signer,
        new_field_spec=fields.SigFieldSpec(sig_field_name="FictionalSignature"),
    )
    return output.getvalue(), certificate_pem


def _tamper_after_signing(data: bytes) -> bytes:
    writer = IncrementalPdfFileWriter(io.BytesIO(data))
    writer.root[generic.pdf_name("/DocuVerifyChangedAfterSigning")] = generic.pdf_string("yes")
    writer.update_root()
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_pdf_signatures_distinguish_unsigned_unknown_trusted_and_modified(
    tmp_path: Path,
) -> None:
    trust_store = tmp_path / "trust"
    trust_store.mkdir()
    unsigned = inspect_pdf_signatures(_pdf_upload(_unsigned_pdf()), trust_store)
    assert unsigned.status is PdfSignatureStatus.UNSIGNED

    signed, certificate_pem = _signed_pdf()
    unknown = inspect_pdf_signatures(_pdf_upload(signed), trust_store)
    assert unknown.status is PdfSignatureStatus.VALID_UNKNOWN_TRUST
    assert unknown.signature_count == 1
    assert unknown.checks[0].cryptographically_intact is True

    (trust_store / "fictional-root.pem").write_bytes(certificate_pem)
    trusted = inspect_pdf_signatures(_pdf_upload(signed), trust_store)
    assert trusted.status is PdfSignatureStatus.VALID_TRUSTED
    assert trusted.checks[0].signer_locally_trusted is True

    modified = inspect_pdf_signatures(_pdf_upload(_tamper_after_signing(signed)), trust_store)
    assert modified.status in {PdfSignatureStatus.MODIFIED, PdfSignatureStatus.INVALID}
    assert modified.status is not PdfSignatureStatus.VALID_TRUSTED


def test_qr_payload_is_redacted_and_visible_mismatch_is_separate_from_crypto(
    tmp_path: Path,
) -> None:
    repository = _profile_repository(tmp_path)
    original = repository.get("in.itd.pan-card.v1")
    assert original is not None
    manifest = copy.deepcopy(original.manifest)
    manifest["codes"]["required_keys"] = ["name"]
    manifest["codes"]["issuer_prefixes"] = []
    profile = DocumentProfile(
        original.profile_id,
        manifest,
        original.fingerprint,
        True,
        original.source_name,
        None,
    )
    encoder = cv2.QRCodeEncoder_create()
    qr = encoder.encode('{"name":"KAVYA SRINIVASAN","document_number":"ABCDE1234F"}')
    canvas = np.full((720, 520, 3), 255, dtype=np.uint8)
    qr = cv2.resize(qr, (180, 180), interpolation=cv2.INTER_NEAREST)
    canvas[470:650, 310:490] = cv2.cvtColor(qr, cv2.COLOR_GRAY2BGR)
    image_path = tmp_path / "qr.png"
    assert cv2.imwrite(str(image_path), canvas)
    page = SimpleNamespace(image_path=image_path)
    visible = {
        "name": ExtractedField("name", "ARJUN MENON", 99.0, 1, True),
        "document_number": ExtractedField("document_number", "ABCDE1234F", 99.0, 1, True),
    }

    assessment, decoded = analyze_codes([page], profile, visible)

    assert assessment.status is CheckStatus.FAILED
    assert assessment.results[0].decoded is True
    assert assessment.results[0].visible_fields_consistent is False
    assert assessment.results[0].cryptographic_verification_available is False
    assert assessment.results[0].cryptographic_verification_result is CheckStatus.UNSUPPORTED
    serialized = assessment.model_dump_json()
    assert "KAVYA" not in serialized
    assert "ARJUN" not in serialized
    assert decoded["name"] == "KAVYA SRINIVASAN"


def test_logical_rules_fail_deterministically_and_low_ocr_skips_without_risk(
    tmp_path: Path,
) -> None:
    repository = _profile_repository(tmp_path)
    profile = repository.get("generic.university.grade-cgpa.v1")
    assert profile is not None
    fields = {
        "total": ExtractedField("total", "400", 98, 1, False),
        "maximum": ExtractedField("maximum", "500", 98, 1, False),
        "percentage": ExtractedField("percentage", "70", 98, 1, False),
        "cgpa": ExtractedField("cgpa", "12", 98, 1, False),
    }
    confident_page = SimpleNamespace(
        text=TextExtraction(
            text="Statement of Grades Grade Card CGPA",
            words=(),
            source="fixture",
            confidence=0.98,
        )
    )
    failed = evaluate_logical_rules(profile, [confident_page], fields=fields)
    assert failed.status is CheckStatus.FAILED
    assert failed.failed_count == 2
    assert all("400" not in result.model_dump_json() or result.fields_used.get("total") == "400" for result in failed.results)

    uncertain_page = SimpleNamespace(
        text=TextExtraction(
            text="Statement of Grades",
            words=(),
            source="fixture",
            confidence=0.2,
        )
    )
    skipped = evaluate_logical_rules(profile, [uncertain_page], fields=fields)
    assert skipped.failed_count == 0
    assert skipped.skipped_count == len(skipped.results)


def test_metadata_timeline_is_conservative_and_does_not_invent_source() -> None:
    data = _unsigned_pdf(
        {
            "title": "Fictional metadata fixture",
            "creationDate": "D:20260829120000",
            "modDate": "D:20250829120000",
            "producer": "Fictional PDF producer",
        }
    )
    assessment = inspect_metadata(_pdf_upload(data))
    assert assessment.status is CheckStatus.FAILED
    assert any(item.category == "metadata_timeline" for item in assessment.indicators)
    assert "website" not in assessment.explanation.casefold()
    assert all("human editor" not in item.explanation.casefold() for item in assessment.indicators)
