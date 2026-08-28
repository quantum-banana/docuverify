from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend.tests.conftest import wait_for_completion


EXPECTED_STAGES = [
    "validating_uploads",
    "rendering_documents",
    "normalizing_pages",
    "aligning_reference",
    "extracting_text",
    "comparing_structure",
    "localizing_differences",
    "scoring_evidence",
    "preparing_result",
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
    assert all(event["data"]["job_id"] == created["job_id"] for event in events)
    aligning = next(event["data"] for event in events if event["data"]["stage_id"] == "aligning_reference")
    assert aligning["candidate_page_url"].endswith("/assets/candidate-page")
    preview_response = client.get(aligning["candidate_page_url"])
    assert preview_response.status_code == 200
    assert preview_response.content.startswith(b"\x89PNG")
    status = client.get(created["status_url"]).json()
    assert status["candidate_page_url"] == aligning["candidate_page_url"]


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
    response = client.get(created["events_url"], headers={"Last-Event-ID": "10"})
    assert response.status_code == 200
    assert response.text == ""
