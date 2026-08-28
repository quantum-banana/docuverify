from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from backend.app.models.contracts import (
    BoundingBox,
    CheckStatus,
    CodeAssessment,
    CodeCheckResult,
    DigitalSignatureAssessment,
    LogicalConsistencyAssessment,
    MetadataAssessment,
    QREvidenceState,
    SimilarityAssessment,
)
from backend.app.services.pipeline import AnalysisManager
from backend.app.services.qr_codes import DecodedCode, analyze_codes


EXPECTED_BOX = {"x": 0.62, "y": 0.58, "width": 0.26, "height": 0.28}


class NoResultQRProvider:
    name = "test_qr_provider"
    supported_symbologies = ("QR",)

    def detect_and_decode(self, image: np.ndarray) -> tuple[DecodedCode, ...]:
        return ()


class UnreadableQRProvider(NoResultQRProvider):
    def detect_and_decode(self, image: np.ndarray) -> tuple[DecodedCode, ...]:
        return (
            DecodedCode(
                "QR",
                "",
                np.asarray([[390, 430], [510, 430], [510, 560], [390, 560]], dtype=np.float32),
                self.name,
            ),
        )


class DecodedQRProvider(NoResultQRProvider):
    def detect_and_decode(self, image: np.ndarray) -> tuple[DecodedCode, ...]:
        return (
            DecodedCode(
                "QR",
                '{"document_number":"FICTIONAL-001"}',
                np.asarray([[390, 430], [510, 430], [510, 560], [390, 560]], dtype=np.float32),
                self.name,
            ),
        )


class UnsupportedProvider:
    name = "unsupported_test_provider"
    supported_symbologies: tuple[str, ...] = ()

    def detect_and_decode(self, image: np.ndarray) -> tuple[DecodedCode, ...]:
        return ()


def _profile(
    tier: str,
    *,
    expectation: str = "required",
    with_region: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        capability_tier=tier,
        manifest={
            "capability_tier": tier,
            "profile_confidence": 88,
            "provenance": {"assurance": "P2"},
            "codes": {
                "qr_expectation": expectation,
                "required_keys": [],
                "issuer_prefixes": [],
                "cryptographic_specification": None,
            },
            "security_regions": {
                "qr": (
                    [{"page": 1, "box": EXPECTED_BOX, "region_id": "qr", "label": "QR"}]
                    if with_region
                    else []
                )
            },
        },
    )


def _page(tmp_path: Path, *, dense_region: bool = False) -> SimpleNamespace:
    image = np.full((700, 600, 3), 255, dtype=np.uint8)
    if dense_region:
        x0, y0, size = 372, 406, 150
        for row in range(15):
            for column in range(15):
                if (row * 7 + column * 11) % 5 < 3:
                    px, py = x0 + column * 10, y0 + row * 10
                    image[py : py + 8, px : px + 8] = 0
    path = tmp_path / ("dense.png" if dense_region else "blank.png")
    assert cv2.imwrite(str(path), image)
    return SimpleNamespace(image_path=path, page_number=1)


def test_detection_decoding_and_crypto_are_independent(tmp_path: Path) -> None:
    assessment, _ = analyze_codes(
        [_page(tmp_path)],
        _profile("metadata_only"),
        providers=(DecodedQRProvider(),),
    )

    assert assessment.status is CheckStatus.PASSED
    assert assessment.results[0].state is QREvidenceState.DETECTED_AND_DECODED
    assert assessment.results[0].detected is True
    assert assessment.results[0].decoded is True
    assert QREvidenceState.CRYPTOGRAPHIC_VERIFICATION_UNAVAILABLE in assessment.states


def test_detected_but_unreadable_lowers_coverage_without_missing_claim(
    tmp_path: Path,
) -> None:
    assessment, _ = analyze_codes(
        [_page(tmp_path)],
        _profile("metadata_only"),
        providers=(UnreadableQRProvider(),),
    )

    assert assessment.status is CheckStatus.WARNING
    assert assessment.coverage_score == 45.0
    assert assessment.results[0].state is QREvidenceState.DETECTED_BUT_UNREADABLE
    assert "missing" not in assessment.explanation.casefold()


def test_metadata_only_detector_silence_is_unverified_not_missing(
    tmp_path: Path,
) -> None:
    assessment, _ = analyze_codes(
        [_page(tmp_path)],
        _profile("metadata_only"),
        providers=(NoResultQRProvider(),),
    )

    assert assessment.status is CheckStatus.WARNING
    assert assessment.results[0].state is QREvidenceState.EXPECTED_REGION_OCCUPIED_UNVERIFIED
    assert "not missing" in assessment.results[0].explanation.casefold()
    assert assessment.coverage_score == 30.0


def test_structural_profile_distinguishes_occupied_and_confirmed_empty_region(
    tmp_path: Path,
) -> None:
    occupied, _ = analyze_codes(
        [_page(tmp_path, dense_region=True)],
        _profile("structural"),
        providers=(NoResultQRProvider(),),
    )
    missing, _ = analyze_codes(
        [_page(tmp_path, dense_region=False)],
        _profile("structural"),
        providers=(NoResultQRProvider(),),
    )

    assert occupied.results[0].state is QREvidenceState.EXPECTED_REGION_OCCUPIED_UNVERIFIED
    assert occupied.status is CheckStatus.WARNING
    assert missing.results[0].state is QREvidenceState.CONFIRMED_MISSING
    assert missing.status is CheckStatus.FAILED
    assert missing.results[0].bounding_box == BoundingBox.model_validate(EXPECTED_BOX)


def test_not_expected_and_unsupported_decoder_have_explicit_states(
    tmp_path: Path,
) -> None:
    not_expected, _ = analyze_codes(
        [_page(tmp_path)],
        _profile("metadata_only", expectation="not_expected"),
        providers=(NoResultQRProvider(),),
    )
    unsupported, _ = analyze_codes(
        [_page(tmp_path)],
        _profile("structural"),
        providers=(UnsupportedProvider(),),
    )

    assert not_expected.states == [QREvidenceState.NOT_EXPECTED]
    assert unsupported.status is CheckStatus.UNSUPPORTED
    assert unsupported.results[0].state is QREvidenceState.DECODER_UNSUPPORTED


def test_qr_findings_use_local_region_and_unlocalized_gap_has_no_marker(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "candidate.png"
    assert cv2.imwrite(str(image_path), np.full((700, 600, 3), 255, dtype=np.uint8))
    registered: list[str] = []
    manager = object.__new__(AnalysisManager)
    manager.store = SimpleNamespace(
        register_asset=lambda _job, asset_id, _path: registered.append(asset_id)
    )
    candidate_page = SimpleNamespace(page_number=1, image_path=image_path)
    neutral_similarity = SimilarityAssessment(explanation="Not requested.")
    common = {
        "job_id": "fictional-job",
        "assets_dir": tmp_path,
        "pages": [SimpleNamespace(page_number=1)],
        "candidate_pages": [candidate_page],
        "profile_search": None,
        "digital": DigitalSignatureAssessment(),
        "metadata": MetadataAssessment(),
        "logical": LogicalConsistencyAssessment(),
        "handwriting": neutral_similarity,
        "signature": neutral_similarity,
    }
    unlocalized = CodeAssessment(
        status=CheckStatus.WARNING,
        expected="required",
        results=[
            CodeCheckResult(
                code_index=1,
                page_number=1,
                symbology="QR",
                detected=False,
                decoded=False,
                decoder="test",
                confidence_score=30,
                explanation="Expected region could not be verified.",
                state=QREvidenceState.EXPECTED_REGION_OCCUPIED_UNVERIFIED,
            )
        ],
        states=[QREvidenceState.EXPECTED_REGION_OCCUPIED_UNVERIFIED],
        coverage_score=30,
    )
    assert manager._build_extension_findings(codes=unlocalized, **common) == []

    localized = unlocalized.model_copy(
        update={
            "status": CheckStatus.FAILED,
            "results": [
                unlocalized.results[0].model_copy(
                    update={
                        "state": QREvidenceState.CONFIRMED_MISSING,
                        "bounding_box": BoundingBox.model_validate(EXPECTED_BOX),
                        "confidence_score": 92,
                    }
                )
            ],
            "states": [QREvidenceState.CONFIRMED_MISSING],
        }
    )
    findings = manager._build_extension_findings(codes=localized, **common)
    assert len(findings) == 1
    assert findings[0].bounding_box == BoundingBox.model_validate(EXPECTED_BOX)
    assert findings[0].bounding_box != BoundingBox(x=0, y=0, width=1, height=1)
    assert registered
