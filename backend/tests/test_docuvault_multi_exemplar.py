from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import cv2
import fitz
import pytest
from fastapi.testclient import TestClient

from backend.app.docuvault.matching import ProfileMatcher
from backend.app.docuvault.repository import ProfileRepository
from backend.app.services.documents import extract_page_text, render_document_page, validate_upload
from backend.tests.conftest import wait_for_completion


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVALUATION = PROJECT_ROOT / "samples" / "docuvault-visual-evaluation"


def _repository(tmp_path: Path) -> ProfileRepository:
    repository = ProfileRepository(
        bundled_root=PROJECT_ROOT / "backend" / "docuvault" / "profiles",
        schema_path=PROJECT_ROOT / "backend" / "docuvault" / "schemas" / "profile.v1.schema.json",
        index_path=tmp_path / "profiles.sqlite3",
        project_root=PROJECT_ROOT,
    )
    repository.startup()
    return repository


def _prepared_pages(pdf: Path, tmp_path: Path) -> list[SimpleNamespace]:
    data = pdf.read_bytes()
    upload = validate_upload(
        field="candidate",
        filename=pdf.name,
        content_type="application/pdf",
        data=data,
        max_bytes=len(data),
    )
    pages: list[SimpleNamespace] = []
    for index in range(upload.page_count):
        rendered = render_document_page(upload, index, 1200)
        extraction = extract_page_text(upload, rendered, index)
        image_path = tmp_path / f"page-{index + 1}.png"
        assert cv2.imwrite(str(image_path), rendered.image)
        pages.append(
            SimpleNamespace(
                text=extraction,
                width=rendered.image.shape[1],
                height=rendered.image.shape[0],
                image_path=image_path,
            )
        )
    return pages


@pytest.mark.parametrize("exemplar", ["reference-a", "reference-b"])
def test_matcher_selects_the_compatible_exemplar(
    tmp_path: Path, exemplar: str
) -> None:
    fixture = EVALUATION / "cgpa-certificate" / "truth" / f"{exemplar}.pdf"
    result = ProfileMatcher(_repository(tmp_path)).match(
        _prepared_pages(fixture, tmp_path),
        profile_override="synthetic.lumen-grove.achievement-record.v1",
    )

    assert result.selected is not None
    assert result.selected.selected_exemplar_id == exemplar
    assert result.selected.visual_coverage == 100.0
    assert result.selected.visual_alignment_quality >= 90.0
    assert result.selected.visual_risk_allowed is True
    assert result.selected.exemplar_scores is not None
    assert set(result.selected.exemplar_scores) == {"reference-a", "reference-b"}


def test_multi_page_exemplar_is_selected_as_one_complete_set(tmp_path: Path) -> None:
    fixture = EVALUATION / "passport" / "truth" / "reference-b.pdf"
    repository = _repository(tmp_path)
    result = ProfileMatcher(repository).match(
        _prepared_pages(fixture, tmp_path),
        profile_override="synthetic.docuverify.passport.v1",
    )

    assert result.selected is not None
    assert result.selected.selected_exemplar_id == "reference-b"
    selected_assets = result.selected.profile.assets_for_exemplar("reference-b")
    assert [asset.document_page_number for asset in selected_assets] == [1, 2]
    assert all(asset.asset_page_number == 1 for asset in selected_assets)
    assert result.selected.visual_coverage == 100.0
    assert result.selected.visual_risk_allowed is True


def test_synthetic_reference_is_retrieval_only_for_unrelated_candidate(
    tmp_path: Path,
) -> None:
    fixture = PROJECT_ROOT / "samples" / "synthetic" / "reference.pdf"
    result = ProfileMatcher(_repository(tmp_path)).match(
        _prepared_pages(fixture, tmp_path),
        profile_override="synthetic.lumen-grove.achievement-record.v1",
    )

    assert result.selected is not None
    assert result.selected.visual_risk_allowed is False
    assert result.selected.selected_exemplar_id is None
    assert "fixed_visual" not in result.selected.component_scores
    assert "cannot create tampering risk" in result.selected.explanation.casefold()


def _automatic(
    client: TestClient, fixture: Path, profile_id: str
) -> dict:
    response = client.post(
        "/api/v1/analyses/automatic",
        data={"profile_override": profile_id},
        files={"candidate": (fixture.name, fixture.read_bytes(), "application/pdf")},
    )
    assert response.status_code == 202, response.text
    completed = wait_for_completion(
        client, response.json()["status_url"], timeout_seconds=40
    )
    assert completed["state"] == "completed", completed.get("error")
    return completed["result"]


def test_legitimate_b_is_low_risk_and_fixed_tampering_is_localized(
    client: TestClient,
) -> None:
    root = EVALUATION / "university-marksheet"
    clean = _automatic(
        client,
        root / "truth" / "reference-b.pdf",
        "synthetic.docuverify.university-marksheet.v1",
    )
    tampered = _automatic(
        client,
        root / "questioned" / "b-pasted-background.pdf",
        "synthetic.docuverify.university-marksheet.v1",
    )

    assert clean["overall_tampering_risk"] <= 15
    assert clean["reference_profile"]["selected_exemplar"] == "reference-b"
    assert clean["reference_profile"]["reference_image_available"] is True
    assert tampered["overall_tampering_risk"] >= 50
    findings = [finding for page in tampered["pages"] for finding in page["findings"]]
    assert findings
    assert any(
        finding["category"] in {"background_compositing", "variable_field_style_change"}
        for finding in findings
    )
    assert all(finding["bounding_box"] != {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0} for finding in findings)


def test_partial_verified_exemplar_is_visible_but_cannot_create_visual_risk(
    client: TestClient, tmp_path: Path
) -> None:
    source_path = EVALUATION / "passport" / "truth" / "reference-b.pdf"
    one_page = tmp_path / "passport-reference-b-page-1.pdf"
    with fitz.open(source_path) as source, fitz.open() as output:
        output.insert_pdf(source, from_page=0, to_page=0)
        output.save(one_page)

    result = _automatic(client, one_page, "synthetic.docuverify.passport.v1")
    assessment = result["reference_profile"]
    selected = assessment["selected_profile"]

    assert selected["selected_exemplar_id"] == "reference-b"
    assert selected["visual_risk_allowed"] is False
    assert assessment["reference_image_available"] is True
    assert assessment["differed_items"] == []
    assert "retrieval only" in assessment["visual_tampering_interpretation"].casefold()
    assert any(page["reference_image_url"] for page in result["pages"])
    assert result["page_order_anomalies"] == []
    assert all(
        finding["risk_score"] == 0
        for page in result["pages"]
        for finding in page["findings"]
        if any(
            source in {"pixel_difference", "edge_difference", "text_difference", "page_correspondence"}
            for source in finding["evidence_source"]
        )
    )
