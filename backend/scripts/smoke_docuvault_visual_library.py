"""Run one focused five-family DocuVault visual-library smoke."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.main import create_app


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVALUATION = PROJECT_ROOT / "samples" / "docuvault-visual-evaluation"
CASES = (
    ("marksheet", "cbse-class10", "synthetic.docuverify.cbse-class10.v1", 1),
    ("identity", "voter-card", "synthetic.docuverify.voter-card.v1", 2),
    ("certificate", "degree-certificate", "synthetic.docuverify.degree-certificate.v1", 1),
    ("receipt", "fee-receipt", "synthetic.docuverify.fee-receipt.v1", 1),
    ("multi_page", "passport", "synthetic.docuverify.passport.v1", 2),
)


def _wait(client: TestClient, status_url: str) -> dict:
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        response = client.get(status_url)
        response.raise_for_status()
        payload = response.json()
        if payload["state"] in {"completed", "failed"}:
            return payload
        time.sleep(0.03)
    raise TimeoutError("visual-library smoke did not complete")


def run_smoke() -> dict:
    results: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="docuverify-visual-smoke-") as temporary:
        settings = Settings(
            runtime_dir=Path(temporary) / "runtime",
            max_upload_mb=15,
            retention_hours=1,
            worker_count=2,
            max_render_dimension=1200,
            cors_origins=("http://127.0.0.1:5173",),
            ocr_provider_preference="auto",
            ocr_device="cpu",
        )
        with TestClient(create_app(settings)) as client:
            for label, folder, profile_id, expected_pages in CASES:
                fixture = EVALUATION / folder / "truth" / "reference-b.pdf"
                response = client.post(
                    "/api/v1/analyses/automatic",
                    data={"profile_override": profile_id},
                    files={
                        "candidate": (
                            fixture.name,
                            fixture.read_bytes(),
                            "application/pdf",
                        )
                    },
                )
                if response.status_code != 202:
                    raise RuntimeError(f"{label} smoke submission failed: {response.text}")
                completed = _wait(client, response.json()["status_url"])
                if completed["state"] != "completed":
                    raise RuntimeError(f"{label} smoke failed: {completed.get('error')}")
                result = completed["result"]
                assessment = result["reference_profile"]
                selected = assessment["selected_profile"]
                findings = [finding for page in result["pages"] for finding in page["findings"]]
                if selected["profile_id"] != profile_id:
                    raise AssertionError(f"{label} selected the wrong profile")
                if assessment["selected_exemplar"] != "reference-b":
                    raise AssertionError(f"{label} did not select reference B")
                if assessment["reference_source_label"] != "Synthetic demonstration reference":
                    raise AssertionError(f"{label} source label is not explicit")
                if not assessment["reference_image_available"]:
                    raise AssertionError(f"{label} reference image is unavailable")
                if result["candidate_page_count"] != expected_pages:
                    raise AssertionError(f"{label} page count changed")
                if result["overall_tampering_risk"] > 15:
                    raise AssertionError(f"{label} clean risk is unexpectedly high")
                if any(
                    finding["bounding_box"]
                    == {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
                    for finding in findings
                ):
                    raise AssertionError(f"{label} emitted a whole-page marker")
                results.append(
                    {
                        "case": label,
                        "profile_id": profile_id,
                        "pages": expected_pages,
                        "exemplar": assessment["selected_exemplar"],
                        "risk": result["overall_tampering_risk"],
                        "coverage": result["analysis_coverage"],
                        "findings": len(findings),
                    }
                )
    return {"status": "passed", "cases": results}


def main() -> int:
    print(json.dumps(run_smoke(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
