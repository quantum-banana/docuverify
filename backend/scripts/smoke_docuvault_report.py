"""Single fictional DocuVault RC2 smoke for profile, viewer and QR-state behavior."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import cv2
import fitz
import numpy as np
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.main import create_app
from backend.app.models.contracts import QREvidenceState
from backend.app.services.qr_codes import DecodedCode, analyze_codes


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC = PROJECT_ROOT / "samples" / "synthetic"


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
            raise RuntimeError(job.get("error", {}).get("message", "analysis failed"))
        time.sleep(0.03)
    raise TimeoutError("DocuVault report smoke exceeded 90 seconds")


def _automatic(
    client: TestClient,
    payload: bytes,
    filename: str,
    *,
    profile_override: str | None = None,
) -> dict[str, Any]:
    data = {"profile_override": profile_override} if profile_override else {}
    response = client.post(
        "/api/v1/analyses/automatic",
        data=data,
        files={"candidate": (filename, payload, "application/pdf")},
    )
    response.raise_for_status()
    return _wait(client, response.json()["status_url"])


def _aadhaar_style_probe() -> bytes:
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((58, 72), "UNIQUE IDENTIFICATION AUTHORITY OF INDIA", fontsize=16)
    page.insert_text((58, 110), "AADHAAR IDENTITY DOCUMENT", fontsize=14)
    page.insert_text((58, 170), "Name: FICTIONAL PERSON", fontsize=11)
    page.insert_text((58, 198), "Date of Birth: 01/01/2000", fontsize=11)
    page.insert_text((58, 226), "Identity number: 0000 0000 0000", fontsize=11)
    # A dense, deliberately non-QR machine-readable-looking code occupies the
    # configured region; detector failure must never become a missing claim.
    for row in range(18):
        for column in range(18):
            if (row * 7 + column * 11) % 5 < 3:
                x0 = 440 + column * 6
                y0 = 582 + row * 6
                page.draw_rect(
                    fitz.Rect(x0, y0, x0 + 5, y0 + 5),
                    fill=(0, 0, 0),
                    color=(0, 0, 0),
                )
    page.insert_text((58, 790), "FICTIONAL TEST DOCUMENT - NOT VALID", fontsize=9)
    payload = document.tobytes()
    document.close()
    return payload


def _unrelated_probe() -> bytes:
    document = fitz.open()
    page = document.new_page(width=700, height=500)
    page.insert_text((60, 70), "FICTIONAL UNRELATED LOCAL TEST PAGE", fontsize=18)
    page.insert_text((60, 120), "Abstract inventory note with no issuing authority", fontsize=11)
    page.draw_circle((500, 280), 75, color=(0.2, 0.2, 0.2), width=3)
    page.draw_rect(fitz.Rect(70, 260, 300, 390), color=(0.2, 0.2, 0.2), width=2)
    payload = document.tobytes()
    document.close()
    return payload


class _UnreadableProvider:
    name = "fictional_unreadable_qr_provider"
    supported_symbologies = ("QR",)

    def detect_and_decode(self, image: np.ndarray) -> tuple[DecodedCode, ...]:
        return (
            DecodedCode(
                "QR",
                "",
                np.asarray([[300, 300], [430, 300], [430, 430], [300, 430]], dtype=np.float32),
                self.name,
            ),
        )


class _NoResultProvider:
    name = "fictional_no_result_provider"
    supported_symbologies = ("QR",)

    def detect_and_decode(self, image: np.ndarray) -> tuple[DecodedCode, ...]:
        return ()


def _qr_profile(tier: str) -> SimpleNamespace:
    return SimpleNamespace(
        capability_tier=tier,
        visual_reference_path=None,
        manifest={
            "capability_tier": tier,
            "profile_confidence": 90,
            "provenance": {"assurance": "P2"},
            "codes": {
                "qr_expectation": "required",
                "required_keys": [],
                "issuer_prefixes": [],
                "cryptographic_specification": None,
            },
            "security_regions": {
                "qr": [
                    {
                        "page": 1,
                        "box": {"x": 0.62, "y": 0.58, "width": 0.26, "height": 0.28},
                    }
                ]
            },
        },
    )


def _direct_qr_states(root: Path) -> tuple[str, str]:
    image = np.full((700, 600, 3), 255, dtype=np.uint8)
    image_path = root / "fictional-qr-state-page.png"
    _require(cv2.imwrite(str(image_path), image), "could not write QR smoke page")
    page = SimpleNamespace(image_path=image_path, page_number=1)
    unreadable, _ = analyze_codes(
        [page], _qr_profile("metadata_only"), providers=(_UnreadableProvider(),)
    )
    missing, _ = analyze_codes(
        [page], _qr_profile("structural"), providers=(_NoResultProvider(),)
    )
    return unreadable.results[0].state.value, missing.results[0].state.value


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="docuverify-docuvault-rc2-") as temporary:
        root = Path(temporary)
        settings = Settings(
            runtime_dir=root / "runtime",
            max_upload_mb=15,
            retention_hours=1,
            worker_count=1,
            max_render_dimension=1200,
            cors_origins=("http://127.0.0.1:5173",),
            ocr_provider_preference="auto",
            ocr_device="cpu",
        )
        with TestClient(create_app(settings)) as client:
            metadata = _automatic(
                client,
                _aadhaar_style_probe(),
                "fictional-aadhaar-style.pdf",
                profile_override="in.uidai.aadhaar-style.v1",
            )
            visual = _automatic(
                client,
                (SYNTHETIC / "template_legitimate_candidate.pdf").read_bytes(),
                "fictional-lumen-candidate.pdf",
                profile_override="synthetic.lumen-grove.achievement-record.v1",
            )
            fallback = _automatic(
                client,
                _unrelated_probe(),
                "fictional-unrelated.pdf",
            )

        metadata_profile = metadata["reference_profile"]["selected_profile"]
        metadata_states = set(metadata["codes"]["states"])
        _require(metadata_profile["capability_tier"] == "metadata_only", "metadata-only tier was not preserved")
        _require(not metadata_profile["visual_reference_available"], "metadata profile invented a visual reference")
        _require(QREvidenceState.CONFIRMED_MISSING.value not in metadata_states, "visible/unverified code was called missing")
        _require(all(page["reference_image_url"] is None for page in metadata["pages"]), "candidate proxy leaked as a reference image")
        _require(
            not any(
                finding["category"].startswith("qr")
                and finding["bounding_box"] == {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
                for page in metadata["pages"]
                for finding in page["findings"]
            ),
            "QR capability gap produced a whole-page marker",
        )

        visual_profile = visual["reference_profile"]["selected_profile"]
        _require(visual_profile["capability_tier"] == "visual_reference", "visual profile tier missing")
        _require(visual_profile["visual_reference_available"], "trusted visual asset unavailable")
        _require(any(page["reference_image_url"] for page in visual["pages"]), "trusted reference viewer asset missing")
        _require(visual_profile["match_level"] == "Strong", "strong match was not reported")
        _require(fallback["reference_profile"]["closest_fallback_used"], "closest-profile fallback was not reported")

        unreadable_state, missing_state = _direct_qr_states(root)
        _require(unreadable_state == QREvidenceState.DETECTED_BUT_UNREADABLE.value, "unreadable QR state failed")
        _require(missing_state == QREvidenceState.CONFIRMED_MISSING.value, "confirmed-missing QR state failed")

        print(
            "DOCUVAULT_SMOKE PASS "
            "metadata_only visual_reference unreadable_qr confirmed_missing_qr "
            "strong_match closest_fallback"
        )


if __name__ == "__main__":
    main()
