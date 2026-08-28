from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import fitz
import numpy as np
import pytest

from backend.app.forensics.alignment import align_reference
from backend.app.forensics.differences import DifferenceRegion, localize_differences
from backend.app.forensics.scoring import aggregate_page_scores, finding_scores
from backend.app.forensics.text import RegionRole, TextChange, compare_text


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC = PROJECT_ROOT / "samples" / "synthetic"
EXPECTED = PROJECT_ROOT / "samples" / "expected"


def _extract_page(path: Path, page_number: int = 1) -> SimpleNamespace:
    with fitz.open(path) as document:
        page = document[page_number - 1]
        width, height = page.rect.width, page.rect.height
        words = tuple(
            SimpleNamespace(
                text=str(word[4]),
                bbox=(
                    float(word[0] / width),
                    float(word[1] / height),
                    float(word[2] / width),
                    float(word[3] / height),
                ),
            )
            for word in page.get_text("words", sort=True)
            if str(word[4]).strip()
        )
        return SimpleNamespace(text=page.get_text("text", sort=True).strip(), words=words)


def _normalized_region(region: DifferenceRegion, width: int, height: int) -> dict[str, float]:
    return {
        "x": region.x0 / width,
        "y": region.y0 / height,
        "width": region.width / width,
        "height": region.height / height,
    }


def _iou(first: dict[str, float], second: dict[str, float]) -> float:
    intersection_width = max(
        0.0,
        min(first["x"] + first["width"], second["x"] + second["width"])
        - max(first["x"], second["x"]),
    )
    intersection_height = max(
        0.0,
        min(first["y"] + first["height"], second["y"] + second["height"])
        - max(first["y"], second["y"]),
    )
    intersection = intersection_width * intersection_height
    union = first["width"] * first["height"] + second["width"] * second["height"] - intersection
    return intersection / union if union else 0.0


def _localize_pair(
    reference_png: Path,
    candidate_png: Path,
    reference_pdf: Path,
    candidate_pdf: Path,
    *,
    page_number: int = 1,
) -> tuple[dict[str, float], float]:
    reference = cv2.imread(str(reference_png), cv2.IMREAD_COLOR)
    candidate = cv2.imread(str(candidate_png), cv2.IMREAD_COLOR)
    assert reference is not None and candidate is not None
    alignment = align_reference(reference, candidate)
    comparison = compare_text(
        _extract_page(reference_pdf, page_number),
        _extract_page(candidate_pdf, page_number),
    )
    localized = localize_differences(
        alignment.aligned_reference,
        candidate,
        comparison.changes,
        alignment.matrix,
    )
    assert localized.regions
    height, width = candidate.shape[:2]
    return _normalized_region(localized.regions[0], width, height), alignment.quality


def test_phase1_golden_localization_iou_is_at_least_point_three() -> None:
    manifest = json.loads((EXPECTED / "manifest.json").read_text(encoding="utf-8"))
    actual, alignment_quality = _localize_pair(
        SYNTHETIC / "reference.png",
        SYNTHETIC / "tampered_candidate.png",
        SYNTHETIC / "reference.pdf",
        SYNTHETIC / "tampered_candidate.pdf",
    )
    expected = manifest["alterations"][0]["normalized_bbox"]
    assert alignment_quality >= 0.95
    assert _iou(actual, expected) == pytest.approx(0.3745511831, abs=0.002)
    assert _iou(actual, expected) >= 0.30


def test_three_page_tampering_localizes_on_page_two() -> None:
    manifest = json.loads((EXPECTED / "phase2_manifest.json").read_text(encoding="utf-8"))
    actual, alignment_quality = _localize_pair(
        SYNTHETIC / "multipage_reference_page_2.png",
        SYNTHETIC / "multipage_tampered_candidate_page_2.png",
        SYNTHETIC / "multipage_reference.pdf",
        SYNTHETIC / "multipage_tampered_candidate.pdf",
        page_number=2,
    )
    expected = manifest["multi_page"]["tampering"]["normalized_bbox"]
    assert alignment_quality >= 0.95
    assert _iou(actual, expected) == pytest.approx(0.5584561077, abs=0.002)
    assert _iou(actual, expected) >= manifest["multi_page"]["tampering"][
        "minimum_localization_iou"
    ]


def test_text_only_reference_box_maps_through_homography() -> None:
    image = np.full((300, 400, 3), 255, dtype=np.uint8)
    reference_box = (0.20, 0.20, 0.30, 0.23)
    change = TextChange(
        before="REMOVED",
        after="(removed)",
        bbox=reference_box,
        reference_bbox=reference_box,
    )
    matrix = np.array(
        [[1.0, 0.0, 40.0], [0.0, 1.0, 20.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    result = localize_differences(image, image.copy(), (change,), matrix)
    assert len(result.regions) == 1
    region = result.regions[0]
    center_x = (region.x0 + region.x1) / 2 / image.shape[1]
    center_y = (region.y0 + region.y1) / 2 / image.shape[0]
    assert center_x == pytest.approx(0.35, abs=0.005)
    assert center_y == pytest.approx(0.2817, abs=0.006)


def test_deleted_text_reference_box_uses_reference_dimensions_before_mapping() -> None:
    candidate = np.full((600, 400, 3), 255, dtype=np.uint8)
    reference_box = (0.20, 0.25, 0.30, 0.30)
    change = TextChange(
        before="REMOVED",
        after="(removed)",
        bbox=reference_box,
        reference_bbox=reference_box,
    )
    # Dimension-fallback transform from a 1000x800 reference raster to the
    # 400x600 candidate raster. The normalized position should remain stable.
    matrix = np.array(
        [[0.4, 0.0, 0.0], [0.0, 0.75, 0.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )

    result = localize_differences(
        candidate,
        candidate.copy(),
        (change,),
        matrix,
        reference_size=(1000, 800),
    )

    assert len(result.regions) == 1
    region = result.regions[0]
    center_x = (region.x0 + region.x1) / 2 / candidate.shape[1]
    center_y = (region.y0 + region.y1) / 2 / candidate.shape[0]
    assert center_x == pytest.approx(0.25, abs=0.005)
    assert center_y == pytest.approx(0.275, abs=0.005)


def test_template_field_suggestions_require_stable_labels() -> None:
    comparison = compare_text(
        _extract_page(SYNTHETIC / "template_reference.pdf"),
        _extract_page(SYNTHETIC / "template_legitimate_candidate.pdf"),
        comparison_mode="template",
    )
    labels = {suggestion.label for suggestion in comparison.region_suggestions}
    assert labels == {"name", "identifier", "issue date", "result"}
    assert all(
        suggestion.role is RegionRole.VARIABLE
        for suggestion in comparison.region_suggestions
    )
    assert all(suggestion.confidence >= 0.90 for suggestion in comparison.region_suggestions)


def test_unlabelled_short_heading_is_fixed_not_variable() -> None:
    bbox = (0.35, 0.10, 0.55, 0.15)
    reference = SimpleNamespace(
        text="OFFICIAL",
        words=(SimpleNamespace(text="OFFICIAL", bbox=bbox),),
    )
    candidate = SimpleNamespace(
        text="ALTERED",
        words=(SimpleNamespace(text="ALTERED", bbox=bbox),),
    )

    comparison = compare_text(reference, candidate, comparison_mode="template")

    assert len(comparison.changes) == 1
    assert comparison.changes[0].role is RegionRole.FIXED
    assert comparison.region_suggestions[0].role is RegionRole.FIXED
    assert comparison.region_suggestions[0].label is None


def test_template_scoring_separates_allowed_values_from_manipulation() -> None:
    common = {
        "changed_pixels": 2400,
        "region_area": 15000,
        "page_area": 1_000_000,
        "mean_delta": 58.0,
        "alignment_quality": 0.98,
        "has_text_change": True,
        "comparison_mode": "template",
        "region_role": "variable",
    }
    legitimate_risk, legitimate_confidence = finding_scores(**common)
    manipulated_risk, manipulated_confidence = finding_scores(
        **common,
        typography_inconsistency=0.85,
        background_compositing=0.90,
    )
    fixed_risk, _ = finding_scores(**{**common, "region_role": "fixed"})
    assert legitimate_risk <= 24
    assert manipulated_risk >= 85
    assert fixed_risk >= 65
    assert legitimate_confidence >= 90
    assert manipulated_confidence >= legitimate_confidence


def test_document_aggregation_preserves_strong_page_evidence() -> None:
    assert aggregate_page_scores([0.0, 88.0, 0.0]) == 88.0
    assert aggregate_page_scores([88.0, 40.0, 10.0]) > 88.0
    assert aggregate_page_scores([]) == 0.0
