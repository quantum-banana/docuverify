from __future__ import annotations

import numpy as np

from backend.app.forensics.alignment import align_reference
from backend.app.forensics.scoring import overall_score, risk_label


def test_low_feature_alignment_falls_back_without_crashing() -> None:
    reference = np.full((400, 300, 3), 255, dtype=np.uint8)
    candidate = np.full((500, 350, 3), 255, dtype=np.uint8)
    result = align_reference(reference, candidate)
    assert result.method == "page_dimension_fallback"
    assert result.aligned_reference.shape == candidate.shape
    assert 0 <= result.quality <= 1


def test_exact_pixels_use_identity_alignment() -> None:
    image = np.full((120, 80, 3), 240, dtype=np.uint8)
    result = align_reference(image, image.copy())
    assert result.method == "exact_pixel_identity"
    assert result.quality == 1.0
    assert np.array_equal(result.matrix, np.eye(3))


def test_score_labels_use_documented_thresholds() -> None:
    assert overall_score([], 0.0) == 0.0
    assert risk_label(0).value == "Low tampering risk"
    assert risk_label(25).value == "Moderate tampering risk"
    assert risk_label(50).value == "High tampering risk"
    assert risk_label(75).value == "Critical tampering risk"
