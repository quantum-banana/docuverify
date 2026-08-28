"""Exercise Phase 2 multi-page behavior against a running backend."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLES = PROJECT_ROOT / "samples" / "synthetic"
MANIFEST = json.loads(
    (PROJECT_ROOT / "samples" / "expected" / "phase2_manifest.json").read_text(
        encoding="utf-8"
    )
)


def _run(
    client: httpx.Client, reference: str, candidate: str
) -> tuple[dict, float]:
    created = client.post(
        "/api/v1/analyses/reference",
        data={"comparison_mode": "exact"},
        files={
            "reference": (
                reference,
                (SAMPLES / reference).read_bytes(),
                "application/pdf",
            ),
            "candidate": (
                candidate,
                (SAMPLES / candidate).read_bytes(),
                "application/pdf",
            ),
        },
    ).raise_for_status().json()
    started = time.perf_counter()
    for _ in range(1200):
        job = client.get(created["status_url"]).raise_for_status().json()
        if job["state"] == "completed":
            return job["result"], time.perf_counter() - started
        if job["state"] == "failed":
            raise RuntimeError(job["error"]["message"])
        time.sleep(0.05)
    raise TimeoutError("multi-page analysis exceeded 60 seconds")


def _iou(first: dict[str, float], second: dict[str, float]) -> float:
    x0, y0 = max(first["x"], second["x"]), max(first["y"], second["y"])
    x1 = min(first["x"] + first["width"], second["x"] + second["width"])
    y1 = min(first["y"] + first["height"], second["y"] + second["height"])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = (
        first["width"] * first["height"]
        + second["width"] * second["height"]
        - intersection
    )
    return intersection / union if union else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    reference = "multipage_reference.pdf"
    cases = {
        "clean": "multipage_clean_candidate.pdf",
        "tampered": "multipage_tampered_candidate.pdf",
        "missing": "multipage_missing_candidate.pdf",
        "added": "multipage_added_candidate.pdf",
        "reordered": "multipage_reordered_candidate.pdf",
    }
    results: dict[str, dict] = {}
    with httpx.Client(base_url=args.base_url, timeout=75) as client:
        for name, candidate in cases.items():
            result, wall = _run(client, reference, candidate)
            results[name] = {
                "risk": result["overall_tampering_risk"],
                "duration_ms": result["processing_duration_ms"],
                "wall_seconds": round(wall, 3),
                "page_count": result["total_page_count"],
                "page_risks": [page["risk_score"] for page in result["pages"]],
                "page_findings": [
                    page["finding_count"] for page in result["pages"]
                ],
                "statuses": [page["status"] for page in result["pages"]],
                "anomalies": [
                    item["anomaly_type"]
                    for item in result["page_order_anomalies"]
                ],
            }
            if name == "tampered":
                expected = MANIFEST["multi_page"]["tampering"]["normalized_bbox"]
                page_two_findings = result["pages"][1]["findings"]
                if not page_two_findings:
                    raise RuntimeError("tampered smoke returned no page-2 finding")
                page_two_iou = max(
                    _iou(finding["bounding_box"], expected)
                    for finding in page_two_findings
                )
                results[name]["page_2_iou"] = round(page_two_iou, 4)

    thresholds = MANIFEST["multi_page"]["thresholds"]
    if results["clean"]["risk"] > thresholds["clean_max_risk"]:
        raise RuntimeError("clean multi-page risk exceeded the manifest threshold")
    if results["tampered"]["risk"] < thresholds["tampered_min_risk"]:
        raise RuntimeError("tampered multi-page risk missed the manifest threshold")
    minimum_iou = MANIFEST["multi_page"]["tampering"]["minimum_localization_iou"]
    if results["tampered"]["page_2_iou"] < minimum_iou:
        raise RuntimeError(
            "tampered page-2 localization IoU missed the manifest threshold"
        )
    expected_categories = {
        "missing": "page_missing",
        "added": "page_added",
        "reordered": "page_reordered",
    }
    for name, category in expected_categories.items():
        if category not in results[name]["anomalies"]:
            raise RuntimeError(f"{name} smoke did not report {category}")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
