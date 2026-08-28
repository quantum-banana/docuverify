from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import pytest

from backend.app.docuvault.matching import ProfileMatcher
from backend.app.docuvault.repository import ProfileRepository
from backend.app.docuvault.safe_paths import UnsafeProfilePath, portable_relative_path
from backend.app.services.documents import (
    extract_page_text,
    render_document_page,
    validate_upload,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _repository(tmp_path: Path, external: Path | None = None) -> ProfileRepository:
    repository = ProfileRepository(
        bundled_root=PROJECT_ROOT / "backend" / "docuvault" / "profiles",
        schema_path=PROJECT_ROOT / "backend" / "docuvault" / "schemas" / "profile.v1.schema.json",
        index_path=tmp_path / "profile-index.sqlite3",
        project_root=PROJECT_ROOT,
        external_root=external,
    )
    repository.startup()
    return repository


def test_profile_catalog_is_strict_complete_and_deterministic(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    assert repository.stats() == {
        "profiles": 20,
        "enabled": 20,
        "invalid": 0,
        "families": 19,
        "with_visual_reference": 1,
        "metadata_only": 19,
        "structural": 0,
        "visual_reference": 1,
        "cryptographic": 0,
    }
    first = repository.fingerprints()
    repository.reload()
    assert repository.fingerprints() == first
    assert list(first) == sorted(first)
    assert len(set(first.values())) == len(first)
    aadhaar = repository.get("in.uidai.aadhaar-style.v1")
    lumen = repository.get("synthetic.lumen-grove.achievement-record.v1")
    assert aadhaar is not None and aadhaar.capability_tier == "metadata_only"
    assert aadhaar.reference_assets == ()
    assert lumen is not None and lumen.capability_tier == "visual_reference"
    assert len(lumen.reference_assets) == 1
    assert lumen.reference_assets[0].profile_id == lumen.profile_id
    assert lumen.reference_assets[0].precomputed_fingerprint["algorithm"] == "phash-64-fixed-v1"


def test_profile_state_and_search_are_persisted_without_changing_manifests(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    profile_id = "in.cbse.class10.generic.v1"
    fingerprint = repository.fingerprints()[profile_id]
    repository.set_enabled(profile_id, False)
    assert repository.get(profile_id) is None
    assert repository.get(profile_id, include_disabled=True) is not None
    repository.reload()
    assert repository.get(profile_id) is None
    assert repository.fingerprints()[profile_id] == fingerprint
    matches = repository.search(issuer="CBSE", language="en")
    assert all(profile.profile_id != profile_id for profile in matches)


@pytest.mark.parametrize(
    "value",
    ["../profile.json", "C:/profile.json", "profiles\\x.json", "/profile.json", "profiles/con.json"],
)
def test_profile_paths_reject_escape_and_windows_aliases(value: str) -> None:
    with pytest.raises(UnsafeProfilePath):
        portable_relative_path(value)


def test_invalid_external_profile_is_diagnosed_without_blocking_safe_profiles(
    tmp_path: Path,
) -> None:
    external = tmp_path / "external"
    profile_dir = external / "profiles"
    profile_dir.mkdir(parents=True)
    (profile_dir / "invalid.profile.json").write_text(
        json.dumps({"schema_version": "1.0.0", "profile_id": "broken.profile"}),
        encoding="utf-8",
    )
    repository = _repository(tmp_path / "runtime", external)
    assert repository.stats()["profiles"] == 20
    assert repository.stats()["invalid"] == 1
    assert repository.diagnostics[0].code == "invalid_profile"
    assert "schema violation" in repository.diagnostics[0].message


def test_matching_uses_document_evidence_not_filename_and_returns_top_three(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    fixture = PROJECT_ROOT / "samples" / "synthetic" / "template_legitimate_candidate.pdf"
    data = fixture.read_bytes()
    upload = validate_upload(
        field="candidate",
        filename="intentionally-unrelated-filename.pdf",
        content_type="application/pdf",
        data=data,
        max_bytes=len(data),
    )
    rendered = render_document_page(upload, 0, 1200)
    extraction = extract_page_text(upload, rendered, 0)
    image_path = tmp_path / "candidate.png"
    assert cv2.imwrite(str(image_path), rendered.image)
    page = SimpleNamespace(
        text=extraction,
        width=rendered.image.shape[1],
        height=rendered.image.shape[0],
        image_path=image_path,
    )

    result = ProfileMatcher(repository).match([page])

    assert result.selected is not None
    assert result.selected.profile.profile_id == "synthetic.lumen-grove.achievement-record.v1"
    assert result.selected.score >= 85
    assert len(result.matches) == 3
    assert result.matches[0].component_scores["fixed_visual"] >= 90
    assert result.closest_fallback_used is True
    assert "Strongest signals" in result.selected.explanation


def test_metadata_only_match_does_not_invent_visual_or_structural_scores(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    fixture = PROJECT_ROOT / "samples" / "synthetic" / "template_legitimate_candidate.pdf"
    data = fixture.read_bytes()
    upload = validate_upload(
        field="candidate",
        filename="metadata-only.pdf",
        content_type="application/pdf",
        data=data,
        max_bytes=len(data),
    )
    rendered = render_document_page(upload, 0, 900)
    extraction = extract_page_text(upload, rendered, 0)
    image_path = tmp_path / "candidate-metadata.png"
    assert cv2.imwrite(str(image_path), rendered.image)
    page = SimpleNamespace(
        text=extraction,
        width=rendered.image.shape[1],
        height=rendered.image.shape[0],
        image_path=image_path,
    )

    result = ProfileMatcher(repository).match(
        [page], profile_override="in.uidai.aadhaar-style.v1"
    )

    assert result.selected is not None
    assert result.selected.profile.capability_tier == "metadata_only"
    assert "fixed_visual" not in result.selected.component_scores
    assert "security_regions" not in result.selected.component_scores
    assert "layout_anchors" not in result.selected.component_scores
    assert "page_geometry" not in result.selected.component_scores
    assert "used no page-geometry" in result.selected.explanation
