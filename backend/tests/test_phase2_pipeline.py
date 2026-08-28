from __future__ import annotations

import json
from pathlib import Path

import fitz
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.app.models.contracts import DocumentResult, PageResult, PageStatus
from backend.app.services.documents import TextExtraction
from backend.app.services.pipeline import (
    _PreparedPage,
    _analysis_coverage,
    _estimate_page_correspondence,
    aggregate_page_results,
)
from backend.tests.conftest import SYNTHETIC_DIR, wait_for_completion


MANIFEST = json.loads(
    (SYNTHETIC_DIR.parent / "expected" / "phase2_manifest.json").read_text(
        encoding="utf-8"
    )
)


def _analyse(
    client: TestClient,
    reference_name: str,
    candidate_name: str,
    mode: str = "exact",
) -> tuple[dict, dict]:
    response = client.post(
        "/api/v1/analyses/reference",
        data={"comparison_mode": mode},
        files={
            "reference": (
                reference_name,
                (SYNTHETIC_DIR / reference_name).read_bytes(),
                "application/pdf",
            ),
            "candidate": (
                candidate_name,
                (SYNTHETIC_DIR / candidate_name).read_bytes(),
                "application/pdf",
            ),
        },
    )
    assert response.status_code == 202, response.text
    created = response.json()
    job = wait_for_completion(client, created["status_url"], timeout_seconds=40)
    assert job["state"] == "completed", job.get("error")
    return created, job["result"]


def test_three_page_clean_and_page_two_tampering(client: TestClient) -> None:
    reference = "multipage_reference.pdf"
    _, clean = _analyse(client, reference, "multipage_clean_candidate.pdf")
    created, tampered = _analyse(client, reference, "multipage_tampered_candidate.pdf")

    thresholds = MANIFEST["multi_page"]["thresholds"]
    assert clean["total_page_count"] == 3
    assert clean["reference_page_count"] == 3
    assert clean["candidate_page_count"] == 3
    assert len(clean["pages"]) == 3
    assert clean["overall_tampering_risk"] <= thresholds["clean_max_risk"]
    assert tampered["overall_tampering_risk"] >= thresholds["tampered_min_risk"]
    assert tampered["pages"][0]["finding_count"] == 0
    assert tampered["pages"][1]["finding_count"] >= 1
    assert tampered["pages"][2]["finding_count"] == 0
    assert all(
        finding["page_number"] == 2 for finding in tampered["pages"][1]["findings"]
    )
    assert [item["status"] for item in tampered["page_correspondence"]] == [
        "matched",
        "matched",
        "matched",
    ]

    event_body = client.get(created["events_url"]).text
    payloads = [
        json.loads(line[6:])
        for line in event_body.splitlines()
        if line.startswith("data: ")
    ]
    assert {item["page_number"] for item in payloads} == {1, 2, 3}
    assert all(item["total_pages"] == 3 for item in payloads)
    assert any(item["localized_region"] for item in payloads)


@pytest.mark.parametrize(
    ("candidate", "category", "status"),
    [
        ("multipage_missing_candidate.pdf", "page_missing", "missing"),
        ("multipage_added_candidate.pdf", "page_added", "added"),
        ("multipage_reordered_candidate.pdf", "page_reordered", "reordered"),
    ],
)
def test_page_anomalies_are_explicit(
    client: TestClient, candidate: str, category: str, status: str
) -> None:
    _, result = _analyse(client, "multipage_reference.pdf", candidate)
    assert category in {
        anomaly["anomaly_type"] for anomaly in result["page_order_anomalies"]
    }
    assert status in {page["status"] for page in result["pages"]}
    assert category in {
        finding["category"]
        for page in result["pages"]
        for finding in page["findings"]
    }


def test_template_mode_separates_allowed_and_manipulated_values(
    client: TestClient,
) -> None:
    reference = "template_reference.pdf"
    _, exact = _analyse(client, reference, "template_legitimate_candidate.pdf", "exact")
    _, legitimate = _analyse(
        client, reference, "template_legitimate_candidate.pdf", "template"
    )
    _, manipulated = _analyse(
        client, reference, "template_manipulated_candidate.pdf", "template"
    )

    assert legitimate["comparison_mode"] == "template"
    assert legitimate["overall_tampering_risk"] <= MANIFEST["template"]["legitimate"][
        "maximum_tampering_risk"
    ]
    assert legitimate["overall_tampering_risk"] < exact["overall_tampering_risk"]
    assert legitimate["region_suggestions"]
    assert any(
        finding["category"] == "variable_value_change"
        for finding in legitimate["pages"][0]["findings"]
    )
    assert manipulated["overall_tampering_risk"] >= MANIFEST["template"][
        "manipulated"
    ]["minimum_tampering_risk"]
    assert {
        finding["category"] for finding in manipulated["pages"][0]["findings"]
    } & {"typography_inconsistency", "background_compositing"}


def test_ten_pages_are_accepted_and_eleven_are_rejected(client: TestClient) -> None:
    ten = _pdf_with_content(10)
    accepted = client.post(
        "/api/v1/analyses/reference",
        data={"comparison_mode": "exact"},
        files={
            "reference": ("ten.pdf", ten, "application/pdf"),
            "candidate": ("ten.pdf", ten, "application/pdf"),
        },
    )
    assert accepted.status_code == 202
    completed = wait_for_completion(
        client, accepted.json()["status_url"], timeout_seconds=40
    )
    assert completed["state"] == "completed"
    assert completed["result"]["total_page_count"] == 10

    eleven = _pdf_with_content(11)
    rejected = client.post(
        "/api/v1/analyses/reference",
        data={"comparison_mode": "exact"},
        files={
            "reference": ("eleven.pdf", eleven, "application/pdf"),
            "candidate": ("eleven.pdf", eleven, "application/pdf"),
        },
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "page_limit_exceeded"
    assert rejected.json()["error"]["details"] == {"page_count": 11, "max_pages": 10}


def test_ocr_failure_reduces_coverage_without_raising_risk() -> None:
    succeeded = TextExtraction(
        text="useful text",
        words=(),
        source="rapidocr_onnxruntime",
        confidence=0.9,
        succeeded=True,
        coverage=0.9,
    )
    failed = TextExtraction(
        text="",
        words=(),
        source="rapidocr_onnxruntime",
        confidence=None,
        succeeded=False,
        coverage=0.0,
        error="simulated",
    )
    assert _analysis_coverage(failed, failed) < _analysis_coverage(succeeded, succeeded)


def test_document_aggregation_is_deterministic_and_preserves_strong_page() -> None:
    pages = [
        _page(1, 0.0),
        _page(2, 88.0),
        _page(3, 0.0),
    ]
    first = aggregate_page_results(pages, [])
    second = aggregate_page_results(pages, [])
    assert first == second
    assert first.risk_score >= 88.0
    assert first.matched_page_count == 3


def test_equal_page_counts_can_expose_missing_and_unrelated_added_pages() -> None:
    reference_pages = [
        _correspondence_page(1, "orbital archive cover", 0, 0),
        _correspondence_page(2, "trusted examination record", 48, 5),
    ]
    candidate_pages = [
        _correspondence_page(1, "orbital archive cover", 0, 0),
        _correspondence_page(2, "unrelated catering invoice appendix", 255, 15),
    ]

    matches = _estimate_page_correspondence(reference_pages, candidate_pages)

    assert [(item.reference_index, item.candidate_index, item.status) for item in matches] == [
        (0, 0, PageStatus.MATCHED),
        (None, 1, PageStatus.ADDED),
        (1, None, PageStatus.MISSING),
    ]


def test_equal_count_replacement_has_coherent_api_review_slots(
    client: TestClient,
) -> None:
    reference, candidate = _equal_count_replacement_pair()
    response = client.post(
        "/api/v1/analyses/reference",
        data={"comparison_mode": "exact"},
        files={
            "reference": ("reference.pdf", reference, "application/pdf"),
            "candidate": ("candidate.pdf", candidate, "application/pdf"),
        },
    )
    assert response.status_code == 202, response.text
    created = response.json()
    completed = wait_for_completion(client, created["status_url"], timeout_seconds=40)
    assert completed["state"] == "completed", completed.get("error")
    result = completed["result"]

    assert result["reference_page_count"] == 3
    assert result["candidate_page_count"] == 3
    assert result["total_page_count"] == len(result["pages"]) == 4
    assert [page["page_number"] for page in result["pages"]] == [1, 2, 3, 4]
    assert {page["status"] for page in result["pages"]} >= {"missing", "added"}
    assert {item["status"] for item in result["page_correspondence"]} >= {
        "missing",
        "added",
    }

    events = [
        json.loads(line[6:])
        for line in client.get(created["events_url"]).text.splitlines()
        if line.startswith("data: ")
    ]
    assert all(event["total_pages"] == 3 for event in events)
    assert all(1 <= event["page_number"] <= 3 for event in events)
    assert DocumentResult.model_json_schema()["properties"]["total_page_count"][
        "maximum"
    ] == 20


def test_ten_plus_ten_unrelated_pages_produce_twenty_review_slots(
    client: TestClient,
) -> None:
    reference, candidate = _fully_unrelated_pair(page_count=10)
    response = client.post(
        "/api/v1/analyses/reference",
        data={"comparison_mode": "exact"},
        files={
            "reference": ("reference-ten.pdf", reference, "application/pdf"),
            "candidate": ("candidate-ten.pdf", candidate, "application/pdf"),
        },
    )
    assert response.status_code == 202, response.text
    created = response.json()
    completed = wait_for_completion(client, created["status_url"], timeout_seconds=40)
    assert completed["state"] == "completed", completed.get("error")
    result = completed["result"]

    assert result["reference_page_count"] == 10
    assert result["candidate_page_count"] == 10
    assert result["total_page_count"] == len(result["pages"]) == 20
    assert [page["page_number"] for page in result["pages"]] == list(range(1, 21))
    assert result["document_aggregate"]["missing_page_count"] == 10
    assert result["document_aggregate"]["added_page_count"] == 10
    assert len(result["page_correspondence"]) == 20

    events = [
        json.loads(line[6:])
        for line in client.get(created["events_url"]).text.splitlines()
        if line.startswith("data: ")
    ]
    assert all(event["total_pages"] == 10 for event in events)
    assert all(1 <= event["page_number"] <= 10 for event in events)


def _page(page_number: int, risk: float) -> PageResult:
    return PageResult(
        page_number=page_number,
        status=PageStatus.MATCHED,
        risk_score=risk,
        confidence_score=95.0,
        coverage_score=100.0,
        alignment_quality=99.0,
        width=100,
        height=100,
        reference_image_url="/reference.png",
        candidate_image_url="/candidate.png",
        findings=[],
    )


def _correspondence_page(
    page_number: int, heading: str, tone: int, layout_cell: int
) -> _PreparedPage:
    layout = np.zeros(16, dtype=np.float32)
    layout[layout_cell] = 1.0
    return _PreparedPage(
        page_number=page_number,
        image_path=Path("unused.png"),
        asset_id=f"page-{page_number}",
        transform=None,
        width=600,
        height=800,
        original_width=600,
        original_height=800,
        text=TextExtraction(
            text=heading,
            words=(),
            source="test",
            confidence=1.0,
        ),
        thumbnail=np.full((64, 48), tone, dtype=np.uint8),
        layout=layout,
        heading=heading,
    )


def _pdf_with_content(page_count: int) -> bytes:
    document = fitz.open()
    for page_number in range(1, page_count + 1):
        page = document.new_page(width=360, height=480)
        page.insert_text((40, 60), f"Synthetic page {page_number}")
    payload = document.tobytes(no_new_id=True)
    document.close()
    return payload


def _equal_count_replacement_pair() -> tuple[bytes, bytes]:
    document = fitz.open()
    content = (
        ("ORBITAL ARCHIVE COVER", "Alpha navigation manifest and trusted launch record"),
        ("TRUSTED EXAMINATION RECORD", "Candidate QVX-7319 result distinction"),
        ("VERIFICATION ATTESTATION", "Seal ledger approval and archival checksum"),
    )
    for title, body in content:
        page = document.new_page(width=600, height=800)
        page.insert_text((50, 70), title, fontsize=22)
        page.insert_text((50, 120), body, fontsize=13)
        for y in range(170, 650, 60):
            page.draw_line((50, y), (550, y), color=(0.1, 0.2, 0.5), width=2)
    reference = document.tobytes(no_new_id=True)
    document.close()

    document = fitz.open(stream=reference, filetype="pdf")
    replacement = document[1]
    replacement.draw_rect(
        replacement.rect,
        color=(0.0, 0.0, 0.0),
        fill=(0.0, 0.0, 0.0),
        overlay=True,
    )
    replacement.insert_text(
        (40, 80),
        "UNRELATED CATERING INVOICE APPENDIX",
        fontsize=19,
        color=(1.0, 1.0, 1.0),
        overlay=True,
    )
    replacement.insert_text(
        (40, 130),
        "Kitchen inventory meals delivery totals tax",
        fontsize=12,
        color=(1.0, 1.0, 1.0),
        overlay=True,
    )
    for x in range(40, 560, 50):
        replacement.draw_rect(
            (x, 200, x + 25, 700),
            color=(1.0, 0.6, 0.0),
            fill=(1.0, 0.6, 0.0),
            overlay=True,
        )
    candidate = document.tobytes(no_new_id=True)
    document.close()
    return reference, candidate


def _fully_unrelated_pair(page_count: int) -> tuple[bytes, bytes]:
    reference_document = fitz.open()
    candidate_document = fitz.open()
    for page_number in range(1, page_count + 1):
        reference_page = reference_document.new_page(width=600, height=800)
        reference_page.insert_text(
            (45, 70),
            f"TRUSTED ACADEMIC RECORD REF-{page_number:02d}",
            fontsize=20,
        )
        for y in range(150, 700, 55):
            reference_page.draw_line(
                (45, y), (555, y), color=(0.1, 0.2, 0.6), width=2
            )

        candidate_page = candidate_document.new_page(width=800, height=600)
        candidate_page.draw_rect(
            candidate_page.rect,
            color=(0.0, 0.0, 0.0),
            fill=(0.0, 0.0, 0.0),
        )
        candidate_page.insert_text(
            (35, 60),
            f"UNRELATED CATERING INVOICE CAND-{page_number:02d}",
            fontsize=18,
            color=(1.0, 1.0, 1.0),
        )
        for x in range(35, 760, 55):
            candidate_page.draw_rect(
                (x, 120, x + 28, 550),
                color=(1.0, 0.55, 0.0),
                fill=(1.0, 0.55, 0.0),
            )

    reference = reference_document.tobytes(no_new_id=True)
    candidate = candidate_document.tobytes(no_new_id=True)
    reference_document.close()
    candidate_document.close()
    return reference, candidate
