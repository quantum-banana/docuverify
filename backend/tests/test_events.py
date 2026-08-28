from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend.app.core.storage import JobStore
from backend.app.services import documents
from backend.tests.conftest import SYNTHETIC_DIR, wait_for_completion


EXPECTED_STAGES = [
    "validating_uploads",
    "rendering_documents",
    "normalizing_pages",
    "extracting_text",
    "aligning_reference",
    "comparing_structure",
    "localizing_differences",
    "scoring_evidence",
    "decoding_codes",
    "checking_digital_signatures",
    "inspecting_metadata",
    "validating_field_consistency",
    "comparing_handwriting",
    "comparing_signatures",
    "aggregating_evidence",
    "complete",
]


def _parse_sse(body: str) -> list[dict]:
    events = []
    for block in body.strip().split("\n\n"):
        record: dict = {}
        for line in block.splitlines():
            if line.startswith("id: "):
                record["id"] = int(line[4:])
            elif line.startswith("event: "):
                record["event"] = line[7:]
            elif line.startswith("data: "):
                record["data"] = json.loads(line[6:])
        if "data" in record:
            events.append(record)
    return events


def test_sse_replays_real_stages_for_late_client(client: TestClient) -> None:
    created_response = client.post("/api/v1/demo/reference")
    assert created_response.status_code == 202
    created = created_response.json()
    wait_for_completion(client, created["status_url"])
    response = client.get(created["events_url"])
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(response.text)
    assert [event["data"]["stage_id"] for event in events] == EXPECTED_STAGES
    assert [event["id"] for event in events] == list(range(1, len(events) + 1))
    assert events[-1]["event"] == "complete"
    assert events[-1]["data"]["progress"] == 100
    progress = [event["data"]["progress"] for event in events]
    assert progress == sorted(progress)
    assert all(event["data"]["job_id"] == created["job_id"] for event in events)
    aligning = next(event["data"] for event in events if event["data"]["stage_id"] == "aligning_reference")
    assert aligning["candidate_page_url"].endswith("/assets/candidate-page")
    preview_response = client.get(aligning["candidate_page_url"])
    assert preview_response.status_code == 200
    assert preview_response.content.startswith(b"\x89PNG")
    status = client.get(created["status_url"]).json()
    assert status["candidate_page_url"] == aligning["candidate_page_url"]


def test_multipage_extraction_event_precedes_text_work_and_progress_is_monotonic(
    client: TestClient, monkeypatch,
) -> None:
    timeline: list[dict] = []
    original_append_event = JobStore.append_event
    original_extract_page_text = documents.extract_page_text

    def traced_append_event(self, job_id, *args, **kwargs):
        timeline.append(
            {
                "kind": "event",
                "stage": kwargs.get("stage"),
                "page_number": kwargs.get("page_number", 1),
                "total_pages": kwargs.get("total_pages", 1),
            }
        )
        return original_append_event(self, job_id, *args, **kwargs)

    def traced_extract_page_text(
        upload, rendered, page_index=0, ocr_provider_preference=None
    ):
        preceding_event = next(
            item for item in reversed(timeline) if item["kind"] == "event"
        )
        timeline.append(
            {
                "kind": "extract",
                "field": upload.field,
                "page_number": page_index + 1,
                "preceding_event": preceding_event,
            }
        )
        return original_extract_page_text(
            upload,
            rendered,
            page_index,
            ocr_provider_preference=ocr_provider_preference,
        )

    monkeypatch.setattr(JobStore, "append_event", traced_append_event)
    monkeypatch.setattr(documents, "extract_page_text", traced_extract_page_text)
    response = client.post(
        "/api/v1/analyses/reference",
        data={"comparison_mode": "exact"},
        files={
            "reference": (
                "multipage_reference.pdf",
                (SYNTHETIC_DIR / "multipage_reference.pdf").read_bytes(),
                "application/pdf",
            ),
            "candidate": (
                "multipage_clean_candidate.pdf",
                (SYNTHETIC_DIR / "multipage_clean_candidate.pdf").read_bytes(),
                "application/pdf",
            ),
        },
    )
    assert response.status_code == 202
    created = response.json()
    completed = wait_for_completion(client, created["status_url"], timeout_seconds=40)
    assert completed["state"] == "completed", completed.get("error")

    extractions = [item for item in timeline if item["kind"] == "extract"]
    assert len(extractions) == 6
    for extraction in extractions:
        preceding = extraction["preceding_event"]
        assert preceding["stage"].value == "extracting_text"
        assert preceding["page_number"] == extraction["page_number"]
        assert preceding["total_pages"] == 3

    events = _parse_sse(client.get(created["events_url"]).text)
    progress = [event["data"]["progress"] for event in events]
    assert progress == sorted(progress)


def test_sse_reconnect_uses_last_event_id(client: TestClient) -> None:
    created = client.post("/api/v1/demo/reference").json()
    wait_for_completion(client, created["status_url"])
    response = client.get(created["events_url"], headers={"Last-Event-ID": "7"})
    events = _parse_sse(response.text)
    assert events[0]["id"] == 8
    assert events[-1]["event"] == "complete"


def test_invalid_last_event_id_is_structured(client: TestClient) -> None:
    created = client.post("/api/v1/demo/reference").json()
    response = client.get(created["events_url"], headers={"Last-Event-ID": "not-a-number"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_event_cursor"


def test_negative_last_event_id_is_rejected_before_cursor_merge(
    client: TestClient,
) -> None:
    created = client.post("/api/v1/demo/reference").json()
    response = client.get(created["events_url"], headers={"Last-Event-ID": "-1"})
    assert response.status_code == 400
    assert response.json()["error"] == {
        "code": "invalid_event_cursor",
        "message": "Last-Event-ID must be a non-negative integer.",
        "field": "Last-Event-ID",
        "details": {},
    }


def test_reconnect_after_terminal_event_closes_cleanly(client: TestClient) -> None:
    created = client.post("/api/v1/demo/reference").json()
    wait_for_completion(client, created["status_url"])
    terminal_id = _parse_sse(client.get(created["events_url"]).text)[-1]["id"]
    response = client.get(
        created["events_url"], headers={"Last-Event-ID": str(terminal_id)}
    )
    assert response.status_code == 200
    assert response.text == ""
