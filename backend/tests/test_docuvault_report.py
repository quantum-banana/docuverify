from __future__ import annotations

from pathlib import Path

from backend.app.docuvault.matching import ProfileMatch, ProfileSearchResult
from backend.app.docuvault.repository import ProfileRepository
from backend.app.docuvault.trust import reference_strength
from backend.app.models.contracts import (
    BoundingBox,
    CheckStatus,
    CodeAssessment,
    CodeCheckResult,
    ComparisonMode,
    ProfileCapabilityTier,
    QREvidenceState,
    RegionRole,
)
from backend.app.services.pipeline import (
    _profile_mask_role,
    _profile_match_summary,
    _reference_profile_assessment,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _repository(tmp_path: Path) -> ProfileRepository:
    repository = ProfileRepository(
        bundled_root=PROJECT_ROOT / "backend" / "docuvault" / "profiles",
        schema_path=PROJECT_ROOT / "backend" / "docuvault" / "schemas" / "profile.v1.schema.json",
        index_path=tmp_path / "profiles.sqlite3",
        project_root=PROJECT_ROOT,
    )
    repository.startup()
    return repository


def _match(profile, *, score: float = 72.9) -> ProfileMatch:
    components = {
        "issuer_text": 94.0,
        "headings": 88.0,
        "layout_anchors": 81.0,
        "page_geometry": 100.0,
        "security_regions": 70.0,
    }
    return ProfileMatch(
        profile=profile,
        score=score,
        component_scores=components,
        explanation="Deterministic fictional match explanation.",
        strength=reference_strength(
            provenance=str(profile.manifest["provenance"]["assurance"]),
            match_score=score,
            has_visual_reference=profile.visual_reference_path is not None,
            capability_tier=profile.capability_tier,
        ),
        selected_exemplar_id=("reference-a" if profile.reference_assets else None),
        exemplar_scores=({"reference-a": 96.0, "reference-b": 94.0} if profile.reference_assets else {}),
        visual_coverage=(100.0 if profile.reference_assets else 0.0),
        visual_alignment_quality=(92.0 if profile.reference_assets else 0.0),
        visual_risk_allowed=bool(profile.reference_assets),
        visual_policy_reason="Controlled synthetic candidate policy satisfied.",
    )


def test_metadata_profile_report_is_human_readable_and_capability_honest(
    tmp_path: Path,
) -> None:
    profile = _repository(tmp_path).get("in.uidai.aadhaar-style.v1")
    assert profile is not None
    match = _match(profile)
    search = ProfileSearchResult(
        selected=match,
        matches=(match,),
        closest_fallback_used=False,
        inferred_family=profile.family,
        inferred_issuer=profile.issuer,
    )
    codes = CodeAssessment(
        status=CheckStatus.WARNING,
        expected="required",
        results=[
            CodeCheckResult(
                code_index=1,
                page_number=1,
                symbology="QR",
                bounding_box=BoundingBox(x=0.72, y=0.68, width=0.2, height=0.22),
                detected=False,
                decoded=False,
                decoder="opencv_qrcode_detector",
                confidence_score=35,
                explanation="Expected QR region could not be verified.",
                state=QREvidenceState.EXPECTED_REGION_OCCUPIED_UNVERIFIED,
            )
        ],
        states=[
            QREvidenceState.EXPECTED_REGION_OCCUPIED_UNVERIFIED,
            QREvidenceState.CRYPTOGRAPHIC_VERIFICATION_UNAVAILABLE,
        ],
        coverage_score=30,
    )

    report = _reference_profile_assessment(
        ComparisonMode.DOCUVAULT,
        search,
        codes=codes,
    )

    assert report.selected_profile is not None
    assert report.selected_profile.display_name == "Aadhaar identity document"
    assert report.selected_profile.capability_tier is ProfileCapabilityTier.METADATA_ONLY
    assert report.selected_profile.reference_capability == "Metadata only"
    assert report.selected_profile.visual_reference_available is False
    assert len(report.selected_profile.match_reasons) in {3, 4}
    assert "Trusted visual specimen and fixed regions" not in report.checked_items
    assert "No trusted visual specimen is available for this profile" in report.unverified_items
    assert "Expected QR region could not be verified" in report.unverified_items
    assert "Cryptographic QR verification is not available for this profile" in report.unverified_items
    assert "no trusted reference image" in report.result_summary


def test_visual_profile_exposes_safe_asset_summary_and_declared_masks(
    tmp_path: Path,
) -> None:
    profile = _repository(tmp_path).get(
        "synthetic.lumen-grove.achievement-record.v1"
    )
    assert profile is not None
    summary = _profile_match_summary(_match(profile, score=89.0))

    assert summary.capability_tier is ProfileCapabilityTier.VISUAL_REFERENCE
    assert summary.visual_reference_available is True
    assert summary.reference_asset is not None
    assert summary.reference_asset.source_label == "Synthetic demonstration reference"
    assert summary.reference_asset.demonstration_only is True
    assert summary.selected_exemplar_id == "reference-a"
    assert summary.visual_comparison_coverage == 100.0
    serialized = summary.reference_asset.model_dump_json()
    assert "relative_path" not in serialized
    assert str(PROJECT_ROOT) not in serialized

    variable = _profile_mask_role(
        profile,
        1,
        BoundingBox(x=0.18, y=0.20, width=0.12, height=0.03),
        exemplar_id="reference-a",
    )
    fixed = _profile_mask_role(
        profile,
        1,
        BoundingBox(x=0.25, y=0.08, width=0.16, height=0.05),
        exemplar_id="reference-a",
    )
    assert variable is not None and variable[0] is RegionRole.VARIABLE
    assert fixed is not None and fixed[0] is RegionRole.FIXED
