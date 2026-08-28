from __future__ import annotations

import uuid
from pathlib import Path

import cv2
import numpy as np
import pytest

from backend.app.core.config import Settings
from backend.app.core.storage import JobStore
from backend.app.forensics.alignment import AlignmentResult
from backend.app.forensics.differences import DifferenceRegion, DifferenceResult
from backend.app.forensics.scoring import overall_score
from backend.app.forensics.text import TextChange, compare_text
from backend.app.models.contracts import ComparisonMode, RegionRole
from backend.app.services import pipeline
from backend.app.services.documents import TextExtraction, TextWord
from backend.app.services.pipeline import AnalysisManager


WIDTH = 600
HEIGHT = 800
FONT = cv2.FONT_HERSHEY_SIMPLEX


@pytest.fixture
def finding_context(tmp_path: Path):
    settings = Settings(runtime_dir=tmp_path / "runtime", worker_count=1)
    store = JobStore(settings.runtime_dir, settings.database_path)
    manager = AnalysisManager(settings, store)
    job_id = str(uuid.uuid4())
    store.create_job(job_id)
    assets_dir = store.job_directory(job_id) / "assets"
    assets_dir.mkdir(parents=True)
    try:
        yield manager, job_id, assets_dir
    finally:
        manager.shutdown()


def test_legitimate_variable_value_ignores_only_content(finding_context) -> None:
    manager, job_id, assets_dir = finding_context
    reference_image, reference = _text_document("80")
    candidate_image, candidate = _text_document("92")
    change = compare_text(reference, candidate, comparison_mode="template").changes[0]

    findings, suggestions = _build(
        manager,
        job_id,
        assets_dir,
        reference_image,
        candidate_image,
        reference,
        candidate,
        [_region_for_change(change)],
        ComparisonMode.TEMPLATE,
    )

    assert change.role.value == "variable"
    assert findings == []
    assert len(suggestions) == 1
    assert suggestions[0].role is RegionRole.VARIABLE
    assert 'Reference: "80"' in suggestions[0].reason
    assert 'Candidate: "92"' in suggestions[0].reason
    assert list(assets_dir.glob("*.png")) == []


def test_variable_typography_and_baseline_manipulation_is_localized(
    finding_context,
) -> None:
    manager, job_id, assets_dir = finding_context
    reference_image, reference = _text_document("80")
    candidate_image, candidate = _text_document(
        "92",
        value_scale=1.15,
        value_thickness=3,
        baseline_shift=10,
    )
    change = compare_text(reference, candidate, comparison_mode="template").changes[0]

    findings, _ = _build(
        manager,
        job_id,
        assets_dir,
        reference_image,
        candidate_image,
        reference,
        candidate,
        [_region_for_change(change, padding=10)],
        ComparisonMode.TEMPLATE,
    )

    assert len(findings) == 1
    assert findings[0].category == "typography_inconsistency"
    assert findings[0].risk_score >= 72.0
    assert findings[0].supporting_measurements[
        "typography_inconsistency_score"
    ] >= 0.35
    assert len(list(assets_dir.glob("*.png"))) == 3


@pytest.mark.parametrize(
    ("label", "reference_value", "candidate_value"),
    (
        ("Name", "KAVYA SRINIVASAN", "ARJUN MENON"),
        ("Date", "01/01/2025", "02/02/2026"),
        ("Identifier", "SYN26CS1047", "SYN26CS1089"),
        ("Score", "80", "92"),
    ),
)
def test_every_variable_text_field_checks_weight_and_ink_style(
    finding_context,
    label: str,
    reference_value: str,
    candidate_value: str,
) -> None:
    manager, job_id, assets_dir = finding_context
    reference_image, reference = _text_document(
        reference_value,
        label=label,
        value_thickness=2,
        value_colour=(25, 25, 25),
    )
    candidate_image, candidate = _text_document(
        candidate_value,
        label=label,
        value_thickness=1,
        value_colour=(100, 100, 100),
    )
    change = compare_text(reference, candidate, comparison_mode="template").changes[0]
    assert change.role.value == "variable"

    findings, _ = _build(
        manager,
        job_id,
        assets_dir,
        reference_image,
        candidate_image,
        reference,
        candidate,
        [_region_for_change(change, padding=10)],
        ComparisonMode.TEMPLATE,
    )

    assert len(findings) == 1
    assert findings[0].category == "typography_inconsistency"
    assert findings[0].supporting_measurements[
        "typography_inconsistency_score"
    ] >= 0.35


def test_variable_pasted_background_is_localized(finding_context) -> None:
    manager, job_id, assets_dir = finding_context
    reference_image, reference = _text_document("80")
    candidate_image, candidate = _text_document("92", pasted_background=True)
    change = compare_text(reference, candidate, comparison_mode="template").changes[0]

    findings, _ = _build(
        manager,
        job_id,
        assets_dir,
        reference_image,
        candidate_image,
        reference,
        candidate,
        [_region_for_change(change, padding=12)],
        ComparisonMode.TEMPLATE,
    )

    assert len(findings) == 1
    assert findings[0].category == "background_compositing"
    assert findings[0].risk_score >= 78.0
    assert findings[0].supporting_measurements[
        "background_compositing_score"
    ] >= 0.08
    assert len(list(assets_dir.glob("*.png"))) == 3


def test_legitimate_photo_and_qr_payloads_ignore_content_identity(
    finding_context,
) -> None:
    manager, job_id, assets_dir = finding_context
    reference_image, candidate_image, extraction = _legitimate_media_pair()
    regions = [
        _media_region(90, 480, 220, 680),
        _media_region(390, 480, 540, 680),
    ]

    findings, suggestions = _build(
        manager,
        job_id,
        assets_dir,
        reference_image,
        candidate_image,
        extraction,
        extraction,
        regions,
        ComparisonMode.TEMPLATE,
    )

    assert findings == []
    assert len(suggestions) == 2
    assert all(suggestion.role is RegionRole.VARIABLE for suggestion in suggestions)
    assert list(assets_dir.glob("*.png")) == []


def test_displaced_variable_photo_geometry_is_localized(finding_context) -> None:
    manager, job_id, assets_dir = finding_context
    extraction = _media_extraction()
    reference_image = _blank_page()
    candidate_image = _blank_page()
    _draw_photo(reference_image, (90, 480, 220, 680), variant=0)
    _draw_photo(candidate_image, (108, 480, 238, 680), variant=1)
    _draw_media_labels(reference_image)
    _draw_media_labels(candidate_image)
    region = _media_region(90, 480, 238, 680)

    findings, _ = _build(
        manager,
        job_id,
        assets_dir,
        reference_image,
        candidate_image,
        extraction,
        extraction,
        [region],
        ComparisonMode.TEMPLATE,
    )

    assert len(findings) == 1
    assert findings[0].category in {
        "visual_region_displacement",
        "background_compositing",
    }
    assert findings[0].risk_score >= 70.0
    assert len(list(assets_dir.glob("*.png"))) == 3


def test_exact_mode_remains_strict_for_variable_content(finding_context) -> None:
    manager, job_id, assets_dir = finding_context
    reference_image, reference = _text_document("80")
    candidate_image, candidate = _text_document("92")
    change = compare_text(reference, candidate, comparison_mode="exact").changes[0]

    findings, suggestions = _build(
        manager,
        job_id,
        assets_dir,
        reference_image,
        candidate_image,
        reference,
        candidate,
        [_region_for_change(change)],
        ComparisonMode.EXACT,
    )

    assert suggestions == []
    assert len(findings) == 1
    assert findings[0].category == "text_content_change"
    assert findings[0].region_role is RegionRole.FIXED


@pytest.mark.parametrize(
    ("reference_value", "candidate_value"),
    (("86", "96"), ("8.2", "9.2"), ("B", "A")),
)
def test_exact_minute_mark_grade_and_cgpa_changes_are_localized(
    finding_context,
    reference_value: str,
    candidate_value: str,
) -> None:
    manager, job_id, assets_dir = finding_context
    reference_image, reference = _text_document(reference_value)
    candidate_image, candidate = _text_document(candidate_value)
    change = compare_text(reference, candidate, comparison_mode="exact").changes[0]

    findings, _ = _build(
        manager,
        job_id,
        assets_dir,
        reference_image,
        candidate_image,
        reference,
        candidate,
        [_region_for_change(change)],
        ComparisonMode.EXACT,
    )

    assert len(findings) == 1
    assert findings[0].category == "text_content_change"
    assert _box_overlap_ratio(findings[0].bounding_box, change.bbox) >= 0.8
    assert len(list(assets_dir.glob("*.png"))) == 3


def test_complete_legitimate_cross_person_template_pair_is_low_risk(
    finding_context,
) -> None:
    manager, job_id, assets_dir = finding_context
    reference_image, reference, media_regions = _cross_person_document(variant=0)
    candidate_image, candidate, _ = _cross_person_document(variant=1)
    comparison = compare_text(reference, candidate, comparison_mode="template")
    regions = [
        _region_for_change(change, padding=10)
        for change in comparison.changes
    ] + media_regions

    findings, suggestions = _build(
        manager,
        job_id,
        assets_dir,
        reference_image,
        candidate_image,
        reference,
        candidate,
        regions,
        ComparisonMode.TEMPLATE,
    )

    changed_ratio = sum(region.width * region.height for region in regions) / (
        WIDTH * HEIGHT
    )
    assert findings == []
    assert suggestions
    assert all(suggestion.role is RegionRole.VARIABLE for suggestion in suggestions)
    assert overall_score([], changed_ratio) < 25.0
    assert list(assets_dir.glob("*.png")) == []


def test_complete_cross_person_pair_localizes_only_manipulated_variable(
    finding_context,
) -> None:
    manager, job_id, assets_dir = finding_context
    reference_image, reference, media_regions = _cross_person_document(variant=0)
    candidate_image, candidate, _ = _cross_person_document(
        variant=1,
        manipulated_label="Name",
    )
    comparison = compare_text(reference, candidate, comparison_mode="template")
    regions = [
        _region_for_change(change, padding=10)
        for change in comparison.changes
    ] + media_regions

    findings, _ = _build(
        manager,
        job_id,
        assets_dir,
        reference_image,
        candidate_image,
        reference,
        candidate,
        regions,
        ComparisonMode.TEMPLATE,
    )

    assert len(findings) == 1
    finding = findings[0]
    assert finding.category == "typography_inconsistency"
    assert finding.supporting_measurements["typography_inconsistency_score"] >= 0.35
    assert "value" in finding.explanation.casefold()
    assert _box_contains_point(finding.bounding_box, 0.48, 0.14)
    assert len(list(assets_dir.glob("*.png"))) == 3


def test_multiline_variable_line_spacing_is_examined(finding_context) -> None:
    manager, job_id, assets_dir = finding_context
    reference_image, reference = _multiline_address_document(
        ("12 CEDAR ROAD", "NORTH DISTRICT"),
        line_gap=34,
    )
    candidate_image, candidate = _multiline_address_document(
        ("44 MAPLE ROAD", "SOUTH DISTRICT"),
        line_gap=62,
    )
    comparison = compare_text(reference, candidate, comparison_mode="template")
    region = _region_for_changes(comparison.changes, padding=12)

    findings, _ = _build(
        manager,
        job_id,
        assets_dir,
        reference_image,
        candidate_image,
        reference,
        candidate,
        [region],
        ComparisonMode.TEMPLATE,
    )

    assert len(findings) == 1
    assert findings[0].category == "typography_inconsistency"
    assert findings[0].supporting_measurements[
        "line_spacing_inconsistency_score"
    ] >= 0.35


def test_residual_old_text_is_reported_without_using_value_identity(
    finding_context,
) -> None:
    manager, job_id, assets_dir = finding_context
    reference_image, reference = _text_document("8888")
    candidate_image, candidate = _text_document("96")
    cv2.putText(
        candidate_image,
        "8888",
        (228, 252),
        FONT,
        0.75,
        (145, 145, 145),
        1,
        cv2.LINE_AA,
    )
    change = compare_text(reference, candidate, comparison_mode="template").changes[0]

    findings, _ = _build(
        manager,
        job_id,
        assets_dir,
        reference_image,
        candidate_image,
        reference,
        candidate,
        [_region_for_change(change, padding=14)],
        ComparisonMode.TEMPLATE,
    )

    assert len(findings) == 1
    assert findings[0].supporting_measurements["residual_text_overlap_score"] >= 0.35
    assert "residual" in findings[0].explanation.casefold()


def test_halo_erasure_perimeter_is_measured(finding_context) -> None:
    manager, job_id, assets_dir = finding_context
    reference_image, reference = _text_document("86")
    candidate_image, candidate = _text_document("96")
    value_box = candidate.words[-1].bbox
    x0, y0, x1, y1 = _pixel_box(value_box)
    cv2.rectangle(
        candidate_image,
        (x0 - 7, y0 - 7),
        (x1 + 7, y1 + 7),
        (205, 205, 205),
        thickness=4,
    )
    change = compare_text(reference, candidate, comparison_mode="template").changes[0]

    findings, _ = _build(
        manager,
        job_id,
        assets_dir,
        reference_image,
        candidate_image,
        reference,
        candidate,
        [_region_for_change(change, padding=16)],
        ComparisonMode.TEMPLATE,
    )

    assert len(findings) == 1
    assert findings[0].supporting_measurements["halo_erasure_score"] >= 0.18
    assert findings[0].category in {
        "halo_or_erasure_indicator",
        "background_compositing",
    }


def test_local_compression_noise_inconsistency_is_conservative(
    finding_context,
) -> None:
    manager, job_id, assets_dir = finding_context
    reference_image, reference = _text_document("86")
    candidate_image, candidate = _text_document("96")
    value_box = candidate.words[-1].bbox
    x0, y0, x1, y1 = _pixel_box(value_box)
    rng = np.random.default_rng(20260829)
    patch = candidate_image[y0:y1, x0:x1]
    noise = rng.integers(-35, 1, size=patch.shape[:2], dtype=np.int16)
    patch[:] = np.clip(patch.astype(np.int16) + noise[..., None], 0, 255).astype(
        np.uint8
    )
    _draw_text(candidate_image, "96", 220, 250, scale=0.75, thickness=1)
    change = compare_text(reference, candidate, comparison_mode="template").changes[0]

    findings, _ = _build(
        manager,
        job_id,
        assets_dir,
        reference_image,
        candidate_image,
        reference,
        candidate,
        [_region_for_change(change, padding=14)],
        ComparisonMode.TEMPLATE,
    )

    assert len(findings) == 1
    assert findings[0].supporting_measurements[
        "compression_noise_inconsistency_score"
    ] >= 0.35
    assert findings[0].category == "local_compression_noise_inconsistency"
    assert findings[0].title == "Local compression or noise inconsistency"


def test_non_identity_reference_mapping_keeps_sampling_and_evidence_aligned(
    finding_context,
) -> None:
    manager, job_id, assets_dir = finding_context
    reference_image, reference = _text_document("86", value_thickness=2)
    shift_x, shift_y = 31, 19
    matrix = np.array(
        [[1.0, 0.0, shift_x], [0.0, 1.0, shift_y], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    aligned_reference = cv2.warpPerspective(
        reference_image,
        matrix,
        (WIDTH, HEIGHT),
        borderValue=(255, 255, 255),
    )
    candidate_image = _blank_page()
    label_word = _draw_text(
        candidate_image,
        "Score",
        70 + shift_x,
        250 + shift_y,
        scale=0.65,
        thickness=1,
    )
    value_word = _draw_text(
        candidate_image,
        "96",
        220 + shift_x,
        250 + shift_y,
        scale=0.75,
        thickness=1,
        colour=(95, 95, 95),
    )
    candidate = TextExtraction(
        text="Score 96",
        words=(label_word, value_word),
        source="deterministic_test",
        confidence=1.0,
    )
    change = compare_text(reference, candidate, comparison_mode="template").changes[0]
    mapped = pipeline._map_reference_bbox_to_candidate(
        change.reference_bbox,
        matrix,
        (WIDTH, HEIGHT),
        (WIDTH, HEIGHT),
    )
    assert mapped is not None
    assert mapped[0] == pytest.approx(change.reference_bbox[0] + shift_x / WIDTH)
    assert mapped[1] == pytest.approx(change.reference_bbox[1] + shift_y / HEIGHT)
    region = _region_for_bbox(value_word.bbox, change, padding=12)
    alignment = AlignmentResult(
        aligned_reference=aligned_reference,
        matrix=matrix,
        method="deterministic_translation",
        quality=1.0,
        match_count=100,
        inlier_ratio=1.0,
        reprojection_error=0.0,
    )

    findings, _ = _build(
        manager,
        job_id,
        assets_dir,
        reference_image,
        candidate_image,
        reference,
        candidate,
        [region],
        ComparisonMode.TEMPLATE,
        alignment=alignment,
        reference_size=(WIDTH, HEIGHT),
    )

    assert len(findings) == 1
    assert _box_contains_point(findings[0].bounding_box, *value_word_center(value_word))
    reference_crop = cv2.imread(str(assets_dir / "finding-001-reference.png"))
    candidate_crop = cv2.imread(str(assets_dir / "finding-001-candidate.png"))
    assert reference_crop is not None and int(reference_crop.min()) < 100
    assert candidate_crop is not None and int(candidate_crop.min()) < 140


def test_suspicious_sixth_region_is_scored_before_presentation_limit(
    finding_context,
) -> None:
    manager, job_id, assets_dir = finding_context
    reference_image, reference = _six_field_document(manipulated=False)
    candidate_image, candidate = _six_field_document(manipulated=True)
    changes = sorted(
        compare_text(reference, candidate, comparison_mode="template").changes,
        key=lambda change: change.bbox[1],
    )
    assert len(changes) == 6
    regions = [_region_for_change(change, padding=8) for change in changes]

    findings, _ = _build(
        manager,
        job_id,
        assets_dir,
        reference_image,
        candidate_image,
        reference,
        candidate,
        regions,
        ComparisonMode.TEMPLATE,
    )

    assert len(findings) == 1
    assert findings[0].finding_id == "finding-006"
    candidate_box = changes[-1].candidate_bbox
    assert candidate_box is not None
    assert _box_contains_point(
        findings[0].bounding_box,
        (candidate_box[0] + candidate_box[2]) / 2.0,
        (candidate_box[1] + candidate_box[3]) / 2.0,
    )
    assert len(list(assets_dir.glob("*.png"))) == 3


def _cross_person_document(
    *,
    variant: int,
    manipulated_label: str | None = None,
) -> tuple[np.ndarray, TextExtraction, list[DifferenceRegion]]:
    image = _blank_page()
    values = (
        {
            "Name": ("KAVYA SRINIVASAN",),
            "Identifier": ("SYN26CS1047",),
            "Date": ("01/01/2025",),
            "Address": ("12 CEDAR ROAD", "NORTH DISTRICT"),
            "Marks": ("86",),
            "Grade": ("B",),
        },
        {
            "Name": ("ARJUN MENON",),
            "Identifier": ("SYN26CS1089",),
            "Date": ("02/02/2026",),
            "Address": ("44 MAPLE ROAD", "SOUTH DISTRICT"),
            "Marks": ("96",),
            "Grade": ("A",),
        },
    )[variant]
    baselines = {
        "Name": 105,
        "Identifier": 165,
        "Date": 225,
        "Address": 285,
        "Marks": 385,
        "Grade": 445,
    }
    words: list[TextWord] = []
    for label, field_values in values.items():
        baseline = baselines[label]
        words.append(_draw_text(image, label, 42, baseline, scale=0.58, thickness=1))
        for line_index, value in enumerate(field_values):
            thickness = 3 if manipulated_label == label else 1
            colour = (25, 25, 25) if manipulated_label != label else (90, 90, 90)
            words.append(
                _draw_text(
                    image,
                    value,
                    220,
                    baseline + line_index * 32,
                    scale=0.62,
                    thickness=thickness,
                    colour=colour,
                )
            )
    photo_label = _draw_text(image, "Photo", 24, 570, scale=0.48, thickness=1)
    qr_label = _draw_text(image, "QR", 325, 570, scale=0.48, thickness=1)
    words.extend((photo_label, qr_label))
    photo_box = (90, 540, 220, 735)
    qr_box = (390, 540, 540, 735)
    _draw_photo(image, photo_box, variant=variant)
    _draw_qr(image, qr_box, variant=variant)
    extraction = TextExtraction(
        text=" ".join(word.text for word in words),
        words=tuple(words),
        source="deterministic_test",
        confidence=1.0,
    )
    return image, extraction, [_media_region(*photo_box), _media_region(*qr_box)]


def _multiline_address_document(
    values: tuple[str, str],
    *,
    line_gap: int,
) -> tuple[np.ndarray, TextExtraction]:
    image = _blank_page()
    label = _draw_text(image, "Address", 70, 250, scale=0.62, thickness=1)
    first = _draw_text(image, values[0], 220, 250, scale=0.62, thickness=1)
    second = _draw_text(
        image,
        values[1],
        220,
        250 + line_gap,
        scale=0.62,
        thickness=1,
    )
    extraction = TextExtraction(
        text=f"Address {values[0]} {values[1]}",
        words=(label, first, second),
        source="deterministic_test",
        confidence=1.0,
    )
    return image, extraction


def _six_field_document(*, manipulated: bool) -> tuple[np.ndarray, TextExtraction]:
    reference_values = (
        ("Name", "KAVYA"),
        ("Identifier", "SYN1001"),
        ("Date", "01/01/2025"),
        ("Address", "CEDAR ROAD"),
        ("Marks", "86"),
        ("Grade", "B"),
    )
    candidate_values = (
        ("Name", "ARJUN"),
        ("Identifier", "SYN1089"),
        ("Date", "02/02/2026"),
        ("Address", "MAPLE ROAD"),
        ("Marks", "96"),
        ("Grade", "A"),
    )
    image = _blank_page()
    words: list[TextWord] = []
    for index, (label, value) in enumerate(
        candidate_values if manipulated else reference_values
    ):
        baseline = 95 + index * 86
        words.append(_draw_text(image, label, 55, baseline, scale=0.58, thickness=1))
        words.append(
            _draw_text(
                image,
                value,
                220,
                baseline,
                scale=1.05 if manipulated and index == 5 else 0.65,
                thickness=4 if manipulated and index == 5 else 1,
                colour=(140, 140, 140) if manipulated and index == 5 else (25, 25, 25),
            )
        )
    return image, TextExtraction(
        text=" ".join(word.text for word in words),
        words=tuple(words),
        source="deterministic_test",
        confidence=1.0,
    )


def _text_document(
    value: str,
    *,
    label: str = "Score",
    value_scale: float = 0.75,
    value_thickness: int = 1,
    value_colour: tuple[int, int, int] = (25, 25, 25),
    baseline_shift: int = 0,
    pasted_background: bool = False,
) -> tuple[np.ndarray, TextExtraction]:
    image = _blank_page()
    label_word = _draw_text(image, label, 70, 250, scale=0.65, thickness=1)
    value_word = _word_box(
        value,
        220,
        250 + baseline_shift,
        scale=value_scale,
        thickness=value_thickness,
    )
    if pasted_background:
        x0, y0, x1, y1 = _pixel_box(value_word.bbox)
        cv2.rectangle(
            image,
            (x0 - 10, y0 - 8),
            (x1 + 10, y1 + 8),
            (198, 198, 198),
            thickness=-1,
        )
    _draw_text(
        image,
        value,
        220,
        250 + baseline_shift,
        scale=value_scale,
        thickness=value_thickness,
        colour=value_colour,
    )
    extraction = TextExtraction(
        text=f"{label} {value}",
        words=(label_word, value_word),
        source="deterministic_test",
        confidence=1.0,
    )
    return image, extraction


def _legitimate_media_pair() -> tuple[np.ndarray, np.ndarray, TextExtraction]:
    reference = _blank_page()
    candidate = _blank_page()
    _draw_media_labels(reference)
    _draw_media_labels(candidate)
    _draw_photo(reference, (90, 480, 220, 680), variant=0)
    _draw_photo(candidate, (90, 480, 220, 680), variant=1)
    _draw_qr(reference, (390, 480, 540, 680), variant=0)
    _draw_qr(candidate, (390, 480, 540, 680), variant=1)
    return reference, candidate, _media_extraction()


def _media_extraction() -> TextExtraction:
    return TextExtraction(
        text="Photo QR Code",
        words=(
            TextWord("Photo", (0.04, 0.64, 0.13, 0.68), 1.0),
            TextWord("QR Code", (0.54, 0.64, 0.63, 0.68), 1.0),
        ),
        source="deterministic_test",
        confidence=1.0,
    )


def _draw_media_labels(image: np.ndarray) -> None:
    cv2.putText(image, "Photo", (24, 520), FONT, 0.5, (20, 20, 20), 1, cv2.LINE_AA)
    cv2.putText(image, "QR", (324, 520), FONT, 0.5, (20, 20, 20), 1, cv2.LINE_AA)


def _draw_photo(
    image: np.ndarray, box: tuple[int, int, int, int], *, variant: int
) -> None:
    x0, y0, x1, y1 = box
    fill = (175, 190, 205) if variant == 0 else (145, 175, 160)
    cv2.rectangle(image, (x0, y0), (x1 - 1, y1 - 1), fill, thickness=-1)
    cv2.rectangle(image, (x0, y0), (x1 - 1, y1 - 1), (25, 25, 25), thickness=2)
    center = ((x0 + x1) // 2, y0 + 65)
    cv2.circle(image, center, 28, (75 + 20 * variant,) * 3, thickness=-1)
    cv2.ellipse(
        image,
        ((x0 + x1) // 2, y0 + 145),
        (42, 52),
        0,
        180,
        360,
        (65 + 25 * variant,) * 3,
        thickness=-1,
    )


def _draw_qr(
    image: np.ndarray, box: tuple[int, int, int, int], *, variant: int
) -> None:
    x0, y0, x1, y1 = box
    cv2.rectangle(image, (x0, y0), (x1 - 1, y1 - 1), (255, 255, 255), thickness=-1)
    cv2.rectangle(image, (x0, y0), (x1 - 1, y1 - 1), (20, 20, 20), thickness=2)
    cell = 17
    for row in range(10):
        for column in range(7):
            enabled = (row * (2 + variant) + column + variant) % 3 != 0
            if enabled:
                left = x0 + 9 + column * cell
                top = y0 + 9 + row * cell
                cv2.rectangle(
                    image,
                    (left, top),
                    (left + cell - 3, top + cell - 3),
                    (0, 0, 0),
                    thickness=-1,
                )


def _draw_text(
    image: np.ndarray,
    text: str,
    x: int,
    baseline: int,
    *,
    scale: float,
    thickness: int,
    colour: tuple[int, int, int] = (25, 25, 25),
) -> TextWord:
    cv2.putText(
        image,
        text,
        (x, baseline),
        FONT,
        scale,
        colour,
        thickness,
        cv2.LINE_AA,
    )
    return _word_box(text, x, baseline, scale=scale, thickness=thickness)


def _word_box(
    text: str,
    x: int,
    baseline: int,
    *,
    scale: float,
    thickness: int,
) -> TextWord:
    (width, height), lower = cv2.getTextSize(text, FONT, scale, thickness)
    return TextWord(
        text,
        (
            x / WIDTH,
            (baseline - height) / HEIGHT,
            (x + width) / WIDTH,
            (baseline + lower) / HEIGHT,
        ),
        1.0,
    )


def _pixel_box(bbox: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    return (
        int(bbox[0] * WIDTH),
        int(bbox[1] * HEIGHT),
        int(np.ceil(bbox[2] * WIDTH)),
        int(np.ceil(bbox[3] * HEIGHT)),
    )


def _region_for_change(change: TextChange, *, padding: int = 6) -> DifferenceRegion:
    x0, y0, x1, y1 = _pixel_box(change.bbox)
    return DifferenceRegion(
        max(0, x0 - padding),
        max(0, y0 - padding),
        min(WIDTH, x1 + padding),
        min(HEIGHT, y1 + padding),
        changed_pixels=max(80, (x1 - x0) * (y1 - y0) // 3),
        mean_delta=48.0,
        evidence_sources={"text_change", "visual_difference"},
        text_changes=[change],
    )


def _region_for_changes(
    changes: tuple[TextChange, ...],
    *,
    padding: int,
) -> DifferenceRegion:
    bbox = (
        min(change.bbox[0] for change in changes),
        min(change.bbox[1] for change in changes),
        max(change.bbox[2] for change in changes),
        max(change.bbox[3] for change in changes),
    )
    x0, y0, x1, y1 = _pixel_box(bbox)
    return DifferenceRegion(
        max(0, x0 - padding),
        max(0, y0 - padding),
        min(WIDTH, x1 + padding),
        min(HEIGHT, y1 + padding),
        changed_pixels=max(80, (x1 - x0) * (y1 - y0) // 3),
        mean_delta=48.0,
        evidence_sources={"text_change", "visual_difference"},
        text_changes=list(changes),
    )


def _region_for_bbox(
    bbox: tuple[float, float, float, float],
    change: TextChange,
    *,
    padding: int,
) -> DifferenceRegion:
    x0, y0, x1, y1 = _pixel_box(bbox)
    return DifferenceRegion(
        max(0, x0 - padding),
        max(0, y0 - padding),
        min(WIDTH, x1 + padding),
        min(HEIGHT, y1 + padding),
        changed_pixels=max(80, (x1 - x0) * (y1 - y0) // 3),
        mean_delta=48.0,
        evidence_sources={"text_change", "visual_difference"},
        text_changes=[change],
    )


def _media_region(x0: int, y0: int, x1: int, y1: int) -> DifferenceRegion:
    return DifferenceRegion(
        x0,
        y0,
        x1,
        y1,
        changed_pixels=max(100, (x1 - x0) * (y1 - y0) // 2),
        mean_delta=52.0,
        edge_changed_pixels=400,
        evidence_sources={"visual_difference", "edge_difference"},
    )


def _blank_page() -> np.ndarray:
    return np.full((HEIGHT, WIDTH, 3), 255, dtype=np.uint8)


def _build(
    manager: AnalysisManager,
    job_id: str,
    assets_dir: Path,
    reference_image: np.ndarray,
    candidate_image: np.ndarray,
    reference_text: TextExtraction,
    candidate_text: TextExtraction,
    regions: list[DifferenceRegion],
    mode: ComparisonMode,
    *,
    alignment: AlignmentResult | None = None,
    reference_size: tuple[int, int] | None = None,
):
    mask = np.zeros(candidate_image.shape[:2], dtype=np.uint8)
    for region in regions:
        mask[region.y0 : region.y1, region.x0 : region.x1] = 255
    alignment = alignment or AlignmentResult(
            aligned_reference=reference_image,
            matrix=np.eye(3, dtype=np.float64),
            method="deterministic_test_identity",
            quality=1.0,
            match_count=100,
            inlier_ratio=1.0,
            reprojection_error=0.0,
        )
    differences = DifferenceResult(
        mask=mask,
        intensity_delta=mask.copy(),
        regions=regions,
        global_changed_ratio=float(np.count_nonzero(mask) / mask.size),
        global_mean_delta=0.0,
    )
    return manager._build_findings(
        job_id,
        assets_dir,
        1,
        candidate_image,
        alignment,
        differences,
        reference_text,
        candidate_text,
        mode,
        single_page=True,
        reference_size=reference_size or (reference_image.shape[1], reference_image.shape[0]),
    )


def _box_overlap_ratio(
    box: object,
    bbox: tuple[float, float, float, float],
) -> float:
    left = max(box.x, bbox[0])
    top = max(box.y, bbox[1])
    right = min(box.x + box.width, bbox[2])
    bottom = min(box.y + box.height, bbox[3])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    expected = max(1e-9, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
    return intersection / expected


def _box_contains_point(box: object, x: float, y: float) -> bool:
    return box.x <= x <= box.x + box.width and box.y <= y <= box.y + box.height


def value_word_center(word: TextWord) -> tuple[float, float]:
    return (word.bbox[0] + word.bbox[2]) / 2.0, (word.bbox[1] + word.bbox[3]) / 2.0
