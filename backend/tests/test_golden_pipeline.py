from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.tests.conftest import EXPECTED_DIR, upload_pair, wait_for_completion


def _iou(first: dict[str, float], second: dict[str, float]) -> float:
    first_x1, first_y1 = first["x"] + first["width"], first["y"] + first["height"]
    second_x1, second_y1 = second["x"] + second["width"], second["y"] + second["height"]
    intersection_width = max(0.0, min(first_x1, second_x1) - max(first["x"], second["x"]))
    intersection_height = max(0.0, min(first_y1, second_y1) - max(first["y"], second["y"]))
    intersection = intersection_width * intersection_height
    union = first["width"] * first["height"] + second["width"] * second["height"] - intersection
    return intersection / union if union else 0.0


def test_demo_runs_real_pipeline_and_localizes_expected_change(
    client: TestClient, completed_demo: dict
) -> None:
    result = completed_demo["result"]
    manifest = json.loads((EXPECTED_DIR / "manifest.json").read_text(encoding="utf-8"))
    expected = manifest["alterations"][0]["normalized_bbox"]
    assert result["overall_tampering_risk"] >= manifest["thresholds"]["tampered_min_risk"]
    assert result["finding_count"] >= 1
    finding = result["pages"][0]["findings"][0]
    assert finding["category"] == "text_content_change"
    assert _iou(finding["bounding_box"], expected) >= manifest["thresholds"]["minimum_localization_iou"]
    assert "DISTINCTION" in finding["explanation"]
    assert "PASS" in finding["explanation"]
    assert finding["assets"]["candidate_crop_url"].startswith("/api/v1/analyses/")
    assert result["text_extraction"]["reference_source"] == "pymupdf_embedded_text"


def test_clean_candidate_risk_is_substantially_lower(client: TestClient) -> None:
    tampered_response = upload_pair(client)
    tampered = wait_for_completion(client, tampered_response.json()["status_url"])["result"]
    clean_response = upload_pair(client, candidate_name="clean_candidate.pdf")
    clean = wait_for_completion(client, clean_response.json()["status_url"])["result"]
    assert clean["overall_tampering_risk"] <= 15
    assert clean["finding_count"] == 0
    assert tampered["overall_tampering_risk"] - clean["overall_tampering_risk"] >= 40


def test_same_inputs_produce_stable_scores_and_findings(client: TestClient) -> None:
    results = []
    for _ in range(2):
        response = client.post("/api/v1/demo/reference")
        assert response.status_code == 202
        job = wait_for_completion(client, response.json()["status_url"])
        assert job["state"] == "completed"
        results.append(job["result"])
    first, second = results
    assert first["overall_tampering_risk"] == second["overall_tampering_risk"]
    assert first["alignment_quality"] == second["alignment_quality"]
    assert first["finding_count"] == second["finding_count"]
    assert [item["bounding_box"] for item in first["pages"][0]["findings"]] == [
        item["bounding_box"] for item in second["pages"][0]["findings"]
    ]


def test_every_finding_box_is_inside_normalized_page(completed_demo: dict) -> None:
    for page in completed_demo["result"]["pages"]:
        for finding in page["findings"]:
            box = finding["bounding_box"]
            assert 0 <= box["x"] <= 1
            assert 0 <= box["y"] <= 1
            assert 0 < box["width"] <= 1
            assert 0 < box["height"] <= 1
            assert box["x"] + box["width"] <= 1.000001
            assert box["y"] + box["height"] <= 1.000001


def test_assets_are_allowlisted_and_not_path_addressable(
    client: TestClient, completed_demo: dict
) -> None:
    result = completed_demo["result"]
    valid_url = result["pages"][0]["findings"][0]["assets"]["difference_overlay_url"]
    valid = client.get(valid_url)
    assert valid.status_code == 200
    assert valid.headers["content-type"] == "image/png"
    assert valid.content.startswith(b"\x89PNG")
    job_id = completed_demo["job_id"]
    unknown = client.get(f"/api/v1/analyses/{job_id}/assets/jobs.sqlite3")
    assert unknown.status_code == 404
    traversal = client.get(f"/api/v1/analyses/{job_id}/assets/%2e%2e%2finputs%2freference.pdf")
    assert traversal.status_code in {404, 422}
