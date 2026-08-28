"""Exercise exact/template separation against a running backend."""

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


def _run(client: httpx.Client, candidate: str, mode: str) -> tuple[dict, float]:
    created = client.post(
        "/api/v1/analyses/reference",
        data={"comparison_mode": mode},
        files={
            "reference": (
                "template_reference.pdf",
                (SAMPLES / "template_reference.pdf").read_bytes(),
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
    raise TimeoutError("template analysis exceeded 60 seconds")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    cases = (
        ("legitimate_exact", "template_legitimate_candidate.pdf", "exact"),
        ("legitimate_template", "template_legitimate_candidate.pdf", "template"),
        ("manipulated_template", "template_manipulated_candidate.pdf", "template"),
    )
    summary: dict[str, dict] = {}
    with httpx.Client(base_url=args.base_url, timeout=75) as client:
        for name, candidate, mode in cases:
            result, wall = _run(client, candidate, mode)
            summary[name] = {
                "mode": result["comparison_mode"],
                "risk": result["overall_tampering_risk"],
                "duration_ms": result["processing_duration_ms"],
                "wall_seconds": round(wall, 3),
                "categories": [
                    finding["category"]
                    for page in result["pages"]
                    for finding in page["findings"]
                ],
                "suggested_roles": [
                    suggestion["role"] for suggestion in result["region_suggestions"]
                ],
                "suggested_labels": [
                    suggestion["label"] for suggestion in result["region_suggestions"]
                ],
            }

    exact = summary["legitimate_exact"]
    legitimate = summary["legitimate_template"]
    manipulated = summary["manipulated_template"]
    if legitimate["risk"] > MANIFEST["template"]["legitimate"][
        "maximum_tampering_risk"
    ]:
        raise RuntimeError("legitimate template values were scored too highly")
    if manipulated["risk"] < MANIFEST["template"]["manipulated"][
        "minimum_tampering_risk"
    ]:
        raise RuntimeError("manipulated template field was scored too low")
    minimum_material_risk_gap = 20.0
    if exact["risk"] - legitimate["risk"] < minimum_material_risk_gap:
        raise RuntimeError(
            "exact mode did not materially exceed legitimate template-mode risk"
        )
    expected_labels = {
        field["label"].casefold()
        for field in MANIFEST["template"]["variable_fields"]
    }
    actual_labels = {
        label.casefold() for label in legitimate["suggested_labels"] if label
    }
    labels_cover_manifest = all(
        any(actual == expected or actual.endswith(f" {expected}") for actual in actual_labels)
        for expected in expected_labels
    )
    if (
        len(legitimate["suggested_labels"])
        != len(MANIFEST["template"]["variable_fields"])
        or not labels_cover_manifest
    ):
        raise RuntimeError("legitimate template did not return all four field suggestions")
    if set(legitimate["suggested_roles"]) != {"variable"}:
        raise RuntimeError("legitimate template suggestions were not all variable")
    if not set(manipulated["categories"]) & {
        "typography_inconsistency",
        "background_compositing",
    }:
        raise RuntimeError("manipulated template field lacked an appearance finding")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
