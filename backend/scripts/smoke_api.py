"""Exercise a running backend over HTTP and print a non-secret smoke summary."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLES = PROJECT_ROOT / "samples" / "synthetic"
EXPECTED = PROJECT_ROOT / "samples" / "expected" / "manifest.json"


def wait_for_job(client: httpx.Client, status_url: str) -> tuple[dict, float]:
    started = time.perf_counter()
    for _ in range(600):
        job = client.get(status_url).raise_for_status().json()
        if job["state"] in {"completed", "failed"}:
            return job, time.perf_counter() - started
        time.sleep(0.05)
    raise TimeoutError("analysis did not finish within 30 seconds")


def run_demo(client: httpx.Client) -> tuple[dict, float, list[str]]:
    created = client.post("/api/v1/demo/reference").raise_for_status().json()
    job, wall_seconds = wait_for_job(client, created["status_url"])
    if job["state"] != "completed":
        raise RuntimeError(job["error"]["message"])
    sse = client.get(created["events_url"]).raise_for_status().text
    stages = [
        json.loads(line[6:])["stage_id"]
        for line in sse.splitlines()
        if line.startswith("data: ")
    ]
    return job["result"], wall_seconds, stages


def run_clean(client: httpx.Client) -> tuple[dict, float]:
    created = client.post(
        "/api/v1/analyses/reference",
        data={"comparison_mode": "exact"},
        files={
            "reference": (
                "reference.pdf",
                (SAMPLES / "reference.pdf").read_bytes(),
                "application/pdf",
            ),
            "candidate": (
                "clean_candidate.pdf",
                (SAMPLES / "clean_candidate.pdf").read_bytes(),
                "application/pdf",
            ),
        },
    ).raise_for_status().json()
    job, wall_seconds = wait_for_job(client, created["status_url"])
    if job["state"] != "completed":
        raise RuntimeError(job["error"]["message"])
    return job["result"], wall_seconds


def iou(first: dict[str, float], second: dict[str, float]) -> float:
    x0, y0 = max(first["x"], second["x"]), max(first["y"], second["y"])
    x1 = min(first["x"] + first["width"], second["x"] + second["width"])
    y1 = min(first["y"] + first["height"], second["y"] + second["height"])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = first["width"] * first["height"] + second["width"] * second["height"] - intersection
    return intersection / union if union else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))["alterations"][0][
        "normalized_bbox"
    ]
    with httpx.Client(base_url=args.base_url, timeout=45) as client:
        health = client.get("/api/v1/health").raise_for_status().json()
        diagnostics = client.get("/api/v1/diagnostics").raise_for_status().json()
        first, first_wall, first_stages = run_demo(client)
        clean, clean_wall = run_clean(client)
        second, second_wall, second_stages = run_demo(client)
        first_finding = first["pages"][0]["findings"][0]
        evidence = client.get(first_finding["assets"]["difference_overlay_url"])
        evidence.raise_for_status()
    summary = {
        "health": health["status"],
        "backend_ready": diagnostics["backend_ready"],
        "gpu_detected": diagnostics["gpu_detected"],
        "ocr_provider": diagnostics["ocr_provider"],
        "ocr_device": diagnostics["ocr_device"],
        "first_demo": {
            "risk": first["overall_tampering_risk"],
            "findings": first["finding_count"],
            "duration_ms": first["processing_duration_ms"],
            "wall_seconds": round(first_wall, 3),
            "localization_iou": round(iou(first_finding["bounding_box"], expected), 4),
            "stages": first_stages,
        },
        "clean": {
            "risk": clean["overall_tampering_risk"],
            "findings": clean["finding_count"],
            "duration_ms": clean["processing_duration_ms"],
            "wall_seconds": round(clean_wall, 3),
        },
        "second_demo": {
            "risk": second["overall_tampering_risk"],
            "findings": second["finding_count"],
            "duration_ms": second["processing_duration_ms"],
            "wall_seconds": round(second_wall, 3),
            "stages": second_stages,
        },
        "scores_stable": first["overall_tampering_risk"] == second["overall_tampering_risk"],
        "evidence_png": evidence.content.startswith(b"\x89PNG"),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
