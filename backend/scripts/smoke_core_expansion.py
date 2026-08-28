"""One deterministic local smoke for the complete DocuVerify core expansion.

The script uses only fictional repository fixtures and process-generated
ephemeral material. It never writes keys, decoded payloads or document content
into the repository.
"""

from __future__ import annotations

import copy
import io
import json
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import cv2
import fitz
import numpy as np
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import fields, signers

from backend.app.core.config import Settings
from backend.app.docuvault.repository import DocumentProfile
from backend.app.main import create_app
from backend.app.models.contracts import BoundingBox, CheckStatus, PdfSignatureStatus
from backend.app.services.biometric_similarity import RegionSelection, compare_biometric_regions
from backend.app.services.digital_signatures import inspect_pdf_signatures
from backend.app.services.documents import (
    TextExtraction,
    ValidatedUpload,
    extract_page_text,
    render_document_page,
    validate_upload,
)
from backend.app.services.logical_rules import ExtractedField, evaluate_logical_rules
from backend.app.services.metadata_forensics import inspect_metadata
from backend.app.services.qr_codes import analyze_codes


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLES = PROJECT_ROOT / "samples" / "synthetic"
REGION = BoundingBox(x=0.2, y=0.4, width=0.6, height=1 / 3)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _wait(client: TestClient, status_url: str) -> dict[str, Any]:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        job = client.get(status_url).json()
        if job["state"] == "completed":
            return job["result"]
        if job["state"] == "failed":
            raise RuntimeError(job["error"]["message"])
        time.sleep(0.02)
    raise TimeoutError("integrated smoke analysis exceeded 90 seconds")


def _reference_analysis(
    client: TestClient,
    reference: str,
    candidate: str,
    mode: str,
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/analyses/reference",
        data={"comparison_mode": mode},
        files={
            "reference": (reference, (SAMPLES / reference).read_bytes(), "application/pdf"),
            "candidate": (candidate, (SAMPLES / candidate).read_bytes(), "application/pdf"),
        },
    )
    response.raise_for_status()
    return _wait(client, response.json()["status_url"])


def _automatic_analysis(
    client: TestClient,
    payload: bytes,
    filename: str,
) -> tuple[dict[str, Any], set[str]]:
    response = client.post(
        "/api/v1/analyses/automatic",
        files={"candidate": (filename, payload, "application/pdf")},
    )
    response.raise_for_status()
    created = response.json()
    result = _wait(client, created["status_url"])
    events = client.get(created["events_url"]).text
    stages = {
        json.loads(line[6:])["stage_id"]
        for line in events.splitlines()
        if line.startswith("data: ")
    }
    return result, stages


def _profile_probe_pdf() -> bytes:
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_textbox(
        fitz.Rect(48, 30, 547, 88),
        "Central Board of Secondary Education",
        fontsize=18,
        align=1,
    )
    page.insert_textbox(
        fitz.Rect(48, 92, 547, 150),
        "Secondary School Examination",
        fontsize=15,
        align=1,
    )
    page.insert_text((72, 178), "Statement of Marks - Class 10 Marksheet - CBSE", fontsize=13)
    page.insert_text((72, 230), "SYNTHETIC RETRIEVAL PROBE - NOT A VALID DOCUMENT", fontsize=10)
    page.insert_text((72, 270), "Roll number: SYNTH-0001   Name: FICTIONAL STUDENT", fontsize=10)
    page.draw_rect(fitz.Rect(28, 32, 130, 132), color=(0.1, 0.1, 0.1), width=2)
    page.insert_text((50, 82), "CBSE", fontsize=17)
    page.draw_polyline(
        [(330, 690), (360, 664), (390, 705), (420, 674), (468, 698)],
        color=(0.1, 0.1, 0.1),
        width=2,
    )
    for row in range(7):
        for column in range(7):
            if (row * 3 + column * 5) % 4 < 2:
                x0, y0 = 440 + column * 10, 574 + row * 10
                page.draw_rect(fitz.Rect(x0, y0, x0 + 8, y0 + 8), fill=(0, 0, 0))
    payload = document.tobytes()
    document.close()
    return payload


def _upload_pdf(data: bytes, filename: str = "fixture.pdf") -> ValidatedUpload:
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
    page.insert_text((72, 72), "FICTIONAL DIGITAL SIGNATURE FIXTURE")
    if metadata:
        document.set_metadata(metadata)
    payload = document.tobytes()
    document.close()
    return payload


def _signed_pdf() -> tuple[bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "DocuVerify Ephemeral Fictional Signer")]
    )
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
    password = b"ephemeral-smoke-only"
    bundle = pkcs12.serialize_key_and_certificates(
        b"docuverify-smoke",
        key,
        certificate,
        None,
        serialization.BestAvailableEncryption(password),
    )
    signer = signers.SimpleSigner.load_pkcs12_data(
        bundle, other_certs=[], passphrase=password
    )
    writer = IncrementalPdfFileWriter(io.BytesIO(_unsigned_pdf()))
    output = signers.sign_pdf(
        writer,
        signature_meta=signers.PdfSignatureMetadata(field_name="FictionalSignature"),
        signer=signer,
        new_field_spec=fields.SigFieldSpec(sig_field_name="FictionalSignature"),
    )
    return output.getvalue(), certificate.public_bytes(serialization.Encoding.PEM)


def _modified_signed_pdf(data: bytes) -> bytes:
    writer = IncrementalPdfFileWriter(io.BytesIO(data))
    writer.root[generic.pdf_name("/DocuVerifyChangedAfterSigning")] = generic.pdf_string("yes")
    writer.update_root()
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _image_upload(image: np.ndarray, field: str) -> ValidatedUpload:
    encoded, payload = cv2.imencode(".png", image)
    _require(bool(encoded), "could not encode biometric smoke image")
    return validate_upload(
        field=field,
        filename=f"{field}.png",
        content_type="image/png",
        data=payload.tobytes(),
        max_bytes=2_000_000,
    )


def _signature(variant: int = 0) -> np.ndarray:
    image = np.full((120, 360, 3), 255, dtype=np.uint8)
    if variant < 2:
        offset = variant * 2
        points = np.array(
            [[18, 75], [52, 30 + offset], [82, 88], [118, 35], [150, 76],
             [196, 44 + offset], [236, 70], [286, 48], [338, 62]],
            dtype=np.int32,
        )
        cv2.polylines(image, [points], False, (20, 20, 20), 3, cv2.LINE_AA)
        cv2.ellipse(image, (160, 64 + offset), (52, 25), -8, 20, 330, (20, 20, 20), 2)
    else:
        for x in range(30, 330, 42):
            cv2.line(image, (x, 25), (x + 18, 96), (20, 20, 20), 4)
            cv2.line(image, (x + 18, 96), (x + 34, 32), (20, 20, 20), 4)
    return image


def _handwriting(variant: int = 0) -> np.ndarray:
    image = np.full((120, 360, 3), 255, dtype=np.uint8)
    font = cv2.FONT_HERSHEY_SCRIPT_SIMPLEX if variant < 2 else cv2.FONT_HERSHEY_DUPLEX
    text = "local sample" if variant < 2 else "BLOCK 7429"
    cv2.putText(image, text, (12, 72 + min(variant, 1)), font, 1.35, (20, 20, 20),
                2 if variant < 2 else 4, cv2.LINE_AA)
    cv2.putText(image, "verification" if variant < 2 else "TEST DATA", (46, 106),
                font, 0.7, (20, 20, 20), 1 if variant < 2 else 3, cv2.LINE_AA)
    return image


def _candidate_page(
    root: Path, sample: np.ndarray, name: str, *, background: int = 255
) -> SimpleNamespace:
    page = np.full((360, 600, 3), background, dtype=np.uint8)
    page[144:264, 120:480] = sample
    path = root / f"{name}.png"
    _require(cv2.imwrite(str(path), page), "could not write temporary biometric page")
    return SimpleNamespace(page_number=1, image_path=path)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="docuverify-core-smoke-") as temporary:
        root = Path(temporary)
        settings = Settings(
            runtime_dir=root / "runtime",
            max_upload_mb=15,
            retention_hours=24,
            worker_count=2,
            max_render_dimension=1200,
            cors_origins=("http://127.0.0.1:5173",),
            ocr_provider_preference="auto",
            ocr_device="cpu",
            trust_store_path=root / "pipeline-trust",
        )
        settings.trust_store_path.mkdir(parents=True)
        with TestClient(create_app(settings)) as client:
            exact_clean = _reference_analysis(client, "reference.pdf", "clean_candidate.pdf", "exact")
            exact_tampered = _reference_analysis(client, "reference.pdf", "tampered_candidate.pdf", "exact")
            template_legitimate = _reference_analysis(
                client, "template_reference.pdf", "template_legitimate_candidate.pdf", "template"
            )
            template_manipulated = _reference_analysis(
                client, "template_reference.pdf", "template_manipulated_candidate.pdf", "template"
            )
            multipage = _reference_analysis(
                client, "multipage_reference.pdf", "multipage_tampered_candidate.pdf", "exact"
            )
            strong_profile, stages = _automatic_analysis(
                client, _profile_probe_pdf(), "synthetic-profile-probe.pdf"
            )
            closest_profile, _ = _automatic_analysis(
                client,
                (SAMPLES / "template_legitimate_candidate.pdf").read_bytes(),
                "unrelated-name.pdf",
            )
            repository = client.app.state.profiles

            required_stages = {
                "identifying_document_family", "searching_trusted_profiles",
                "matching_issuer_layout", "decoding_codes", "checking_digital_signatures",
                "inspecting_metadata", "validating_field_consistency", "comparing_handwriting",
                "comparing_signatures", "aggregating_evidence",
            }
            _require(required_stages <= stages, "automatic analysis omitted a core progress stage")

            raster_upload = _upload_pdf(
                (SAMPLES / "raster_only_document.pdf").read_bytes(), "raster_only_document.pdf"
            )
            raster_page = render_document_page(raster_upload, 0, 1200)
            raster_text = extract_page_text(
                raster_upload, raster_page, 0, ocr_provider_preference="rapidocr"
            )

            original_profile = repository.get("in.itd.pan-card.v1")
            _require(original_profile is not None, "QR smoke profile missing")
            qr_manifest = copy.deepcopy(original_profile.manifest)
            qr_manifest["codes"]["required_keys"] = ["document_number"]
            qr_manifest["codes"]["issuer_prefixes"] = []
            qr_profile = DocumentProfile(
                original_profile.profile_id,
                qr_manifest,
                original_profile.fingerprint,
                True,
                original_profile.source_name,
            )
            qr = cv2.QRCodeEncoder_create().encode(
                '{"document_number":"SYNTH-0001"}'
            )
            qr = cv2.resize(qr, (180, 180), interpolation=cv2.INTER_NEAREST)
            qr_canvas = np.full((720, 520, 3), 255, dtype=np.uint8)
            qr_canvas[470:650, 310:490] = cv2.cvtColor(qr, cv2.COLOR_GRAY2BGR)
            qr_path = root / "qr.png"
            _require(cv2.imwrite(str(qr_path), qr_canvas), "could not write temporary QR page")
            qr_assessment, _ = analyze_codes(
                [SimpleNamespace(image_path=qr_path)],
                qr_profile,
                {
                    "document_number": ExtractedField(
                        "document_number", "SYNTH-0002", 99.0, 1, True
                    )
                },
            )

            signed, certificate = _signed_pdf()
            trust_store = root / "trust"
            trust_store.mkdir()
            unknown_signature = inspect_pdf_signatures(_upload_pdf(signed), trust_store)
            (trust_store / "fictional-root.pem").write_bytes(certificate)
            trusted_signature = inspect_pdf_signatures(_upload_pdf(signed), trust_store)
            modified_signature = inspect_pdf_signatures(
                _upload_pdf(_modified_signed_pdf(signed)), trust_store
            )

            metadata = inspect_metadata(
                _upload_pdf(
                    _unsigned_pdf(
                        {
                            "title": "Fictional metadata fixture",
                            "creationDate": "D:20260829120000",
                            "modDate": "D:20250829120000",
                            "producer": "Fictional local producer",
                        }
                    )
                )
            )

            logical_profile = repository.get("generic.university.grade-cgpa.v1")
            _require(logical_profile is not None, "logical smoke profile missing")
            logical = evaluate_logical_rules(
                logical_profile,
                [
                    SimpleNamespace(
                        text=TextExtraction(
                            text="Statement of Grades Grade Card CGPA",
                            words=(),
                            source="fictional_smoke",
                            confidence=0.98,
                        )
                    )
                ],
                fields={
                    "total": ExtractedField("total", "400", 98, 1, False),
                    "maximum": ExtractedField("maximum", "500", 98, 1, False),
                    "percentage": ExtractedField("percentage", "70", 98, 1, False),
                    "cgpa": ExtractedField("cgpa", "12", 98, 1, False),
                },
            )

            selection = (RegionSelection(page_number=1, bounding_box=REGION),)
            handwriting_exemplar = _image_upload(_handwriting(0), "handwriting")
            handwriting_consistent = compare_biometric_regions(
                kind="handwriting",
                candidate_pages=[_candidate_page(root, _handwriting(1), "handwriting-same")],
                exemplars=[handwriting_exemplar],
                profile=None,
                user_regions=selection,
            )
            handwriting_mismatch = compare_biometric_regions(
                kind="handwriting",
                candidate_pages=[_candidate_page(root, _handwriting(2), "handwriting-different")],
                exemplars=[handwriting_exemplar],
                profile=None,
                user_regions=selection,
            )
            signature_exemplars = [
                _image_upload(_signature(0), "signature-1"),
                _image_upload(_signature(1), "signature-2"),
            ]
            signature_consistent = compare_biometric_regions(
                kind="signature",
                candidate_pages=[_candidate_page(root, _signature(0), "signature-same")],
                exemplars=signature_exemplars,
                profile=None,
                user_regions=selection,
            )
            signature_pasted = compare_biometric_regions(
                kind="signature",
                candidate_pages=[
                    _candidate_page(root, _signature(0), "signature-pasted", background=225)
                ],
                exemplars=signature_exemplars,
                profile=None,
                user_regions=selection,
            )
            signature_mismatch = compare_biometric_regions(
                kind="signature",
                candidate_pages=[_candidate_page(root, _signature(2), "signature-different")],
                exemplars=signature_exemplars,
                profile=None,
                user_regions=selection,
            )

        _require(exact_clean["overall_tampering_risk"] <= 15, "exact clean risk regressed")
        _require(exact_tampered["overall_tampering_risk"] >= 60, "exact tamper was missed")
        _require(template_legitimate["overall_tampering_risk"] <= 25, "template values scored too high")
        _require(template_manipulated["overall_tampering_risk"] >= 60, "template manipulation was missed")
        _require(multipage["pages"][1]["finding_count"] >= 1, "multi-page page-2 tamper was missed")
        _require(raster_text.succeeded and raster_text.source != "none", "raster OCR failed")
        _require(
            strong_profile["reference_profile"]["reference_strength"] == "Strong trusted-profile match",
            "authoritative metadata probe did not produce a strong profile match",
        )
        _require(
            closest_profile["reference_profile"]["closest_fallback_used"] is True,
            "closest-profile fallback was not explicit",
        )
        _require(qr_assessment.status is CheckStatus.FAILED, "QR visible mismatch was missed")
        _require("SYNTH-0001" not in qr_assessment.model_dump_json(), "QR payload was not redacted")
        _require(unknown_signature.status is PdfSignatureStatus.VALID_UNKNOWN_TRUST, "unknown signer state failed")
        _require(trusted_signature.status is PdfSignatureStatus.VALID_TRUSTED, "local trust state failed")
        _require(
            modified_signature.status in {PdfSignatureStatus.MODIFIED, PdfSignatureStatus.INVALID},
            "modified signed revision was not rejected",
        )
        _require(metadata.status is CheckStatus.FAILED, "metadata timeline contradiction was missed")
        _require(logical.failed_count >= 2, "logical contradictions were missed")
        _require(
            (handwriting_consistent.similarity_score or 0) > (handwriting_mismatch.similarity_score or 0)
            and handwriting_mismatch.status is CheckStatus.FAILED,
            "handwriting mismatch separation failed",
        )
        _require(
            (signature_consistent.similarity_score or 0) > (signature_mismatch.similarity_score or 0)
            and signature_mismatch.status is CheckStatus.FAILED,
            "signature mismatch separation failed",
        )
        _require(
            (signature_pasted.compositing_score or 0) >= 65,
            "signature compositing indicator was missed",
        )

        summary = {
            "status": "PASS",
            "exact": {
                "clean_risk": exact_clean["overall_tampering_risk"],
                "tampered_risk": exact_tampered["overall_tampering_risk"],
            },
            "template": {
                "legitimate_risk": template_legitimate["overall_tampering_risk"],
                "manipulated_risk": template_manipulated["overall_tampering_risk"],
            },
            "multi_page_ocr": {
                "page_2_findings": multipage["pages"][1]["finding_count"],
                "ocr_provider": raster_text.source,
                "ocr_words": len(raster_text.words),
            },
            "docuvault": {
                "strong_profile": strong_profile["reference_profile"]["selected_profile"]["profile_id"],
                "strong_score": strong_profile["reference_profile"]["selected_profile"]["score"],
                "closest_profile": closest_profile["reference_profile"]["selected_profile"]["profile_id"],
                "top_match_count": len(strong_profile["reference_profile"]["top_matches"]),
            },
            "qr": {"status": qr_assessment.status.value, "payload_redacted": True},
            "digital_signature": {
                "unknown": unknown_signature.status.value,
                "trusted": trusted_signature.status.value,
                "modified": modified_signature.status.value,
            },
            "metadata": metadata.status.value,
            "logical": {"failed": logical.failed_count, "skipped": logical.skipped_count},
            "handwriting": {
                "consistent": handwriting_consistent.similarity_score,
                "mismatch": handwriting_mismatch.similarity_score,
            },
            "signature": {
                "consistent": signature_consistent.similarity_score,
                "mismatch": signature_mismatch.similarity_score,
                "pasted_compositing": signature_pasted.compositing_score,
            },
            "backend_stages": sorted(required_stages),
        }
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
