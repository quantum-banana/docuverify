from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import cv2
import fitz
import numpy as np

from backend.app.services.ocr import get_raster_ocr_provider


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLES = PROJECT_ROOT / "samples"
SYNTHETIC = SAMPLES / "synthetic"
EXPECTED = SAMPLES / "expected"
PHASE1_HASHES = {
    "reference.pdf": "cee9846ef35d9f5b5c9d20bde962008cb2884dd3b8a2186be931841e05968458",
    "clean_candidate.pdf": "cee9846ef35d9f5b5c9d20bde962008cb2884dd3b8a2186be931841e05968458",
    "tampered_candidate.pdf": "003cd93e7c28e4301ba55bb60fcca74ec7ed9c6ce02d4de054f26093b47d53ca",
    "reference.png": "60716dfbd6bb847ef2c840de0bc8f0d4069e0d6e900ea6cbf6514f4919b1b62f",
    "clean_candidate.png": "60716dfbd6bb847ef2c840de0bc8f0d4069e0d6e900ea6cbf6514f4919b1b62f",
    "tampered_candidate.png": "fd3ee8ab3778f10711808c55fc3eb536424d5e7019d84faf6dbaca07d072ecf3",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _phase2_manifest() -> dict:
    return json.loads((EXPECTED / "phase2_manifest.json").read_text(encoding="utf-8"))


def _page_texts(path: Path) -> list[str]:
    with fitz.open(path) as document:
        return [page.get_text("text", sort=True).casefold() for page in document]


def _span(path: Path, needle: str) -> dict:
    with fitz.open(path) as document:
        for block in document[0].get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span["text"] == needle:
                        return span
    raise AssertionError(f"span {needle!r} not found in {path.name}")


def _box_iou(
    expected: dict[str, float], actual: tuple[float, float, float, float]
) -> float:
    expected_x1 = expected["x"] + expected["width"]
    expected_y1 = expected["y"] + expected["height"]
    intersection_width = max(
        0.0, min(expected_x1, actual[2]) - max(expected["x"], actual[0])
    )
    intersection_height = max(
        0.0, min(expected_y1, actual[3]) - max(expected["y"], actual[1])
    )
    intersection = intersection_width * intersection_height
    actual_area = (actual[2] - actual[0]) * (actual[3] - actual[1])
    expected_area = expected["width"] * expected["height"]
    return intersection / (expected_area + actual_area - intersection) if intersection else 0.0


def test_phase1_fixture_bytes_are_unchanged() -> None:
    phase1_manifest = json.loads((EXPECTED / "manifest.json").read_text(encoding="utf-8"))
    assert set(phase1_manifest["files"]) == set(PHASE1_HASHES)
    for filename, expected_hash in PHASE1_HASHES.items():
        assert _sha256(SYNTHETIC / filename) == expected_hash
        assert phase1_manifest["files"][filename]["sha256"] == expected_hash


def test_phase2_manifest_covers_every_generated_file_by_hash_and_size() -> None:
    manifest = _phase2_manifest()
    assert manifest["synthetic"] is True
    assert len(manifest["files"]) == 25
    for relative_name, expected in manifest["files"].items():
        path = SAMPLES / relative_name
        assert path.is_file(), relative_name
        assert path.stat().st_size == expected["bytes"]
        assert _sha256(path) == expected["sha256"]


def test_three_page_clean_pair_is_pixel_identical_and_tamper_is_page_two_only() -> None:
    manifest = _phase2_manifest()
    assert _sha256(SYNTHETIC / "multipage_reference.pdf") == _sha256(
        SYNTHETIC / "multipage_clean_candidate.pdf"
    )
    reference_pages = [
        cv2.imread(str(SYNTHETIC / f"multipage_reference_page_{page}.png"))
        for page in (1, 2, 3)
    ]
    clean_pages = [
        cv2.imread(str(SYNTHETIC / f"multipage_clean_candidate_page_{page}.png"))
        for page in (1, 2, 3)
    ]
    tampered_pages = [
        cv2.imread(str(SYNTHETIC / f"multipage_tampered_candidate_page_{page}.png"))
        for page in (1, 2, 3)
    ]
    assert all(image is not None for image in (*reference_pages, *clean_pages, *tampered_pages))
    assert all(
        np.array_equal(reference, clean)
        for reference, clean in zip(reference_pages, clean_pages)
    )
    assert np.array_equal(reference_pages[0], tampered_pages[0])
    assert not np.array_equal(reference_pages[1], tampered_pages[1])
    assert np.array_equal(reference_pages[2], tampered_pages[2])

    changed = np.any(cv2.absdiff(reference_pages[1], tampered_pages[1]) >= 8, axis=2)
    mask = cv2.imread(
        str(SAMPLES / manifest["multi_page"]["tampering"]["mask"]),
        cv2.IMREAD_GRAYSCALE,
    )
    assert mask is not None and mask.shape == changed.shape
    assert np.count_nonzero(changed) > 500
    assert np.count_nonzero(changed & (mask > 0)) / np.count_nonzero(changed) >= 0.98


def test_page_anomaly_documents_have_declared_sequences() -> None:
    manifest = _phase2_manifest()
    reference_texts = _page_texts(SYNTHETIC / "multipage_reference.pdf")
    assert len(reference_texts) == 3
    for heading, text_content in zip(manifest["multi_page"]["page_sequence"], reference_texts):
        assert heading in text_content

    for anomaly in ("missing", "added", "reordered"):
        specification = manifest["page_anomalies"][anomaly]
        page_texts = _page_texts(SAMPLES / specification["candidate"])
        assert len(page_texts) == len(specification["candidate_sequence"])
        for heading, text_content in zip(specification["candidate_sequence"], page_texts):
            assert heading in text_content


def test_template_legitimate_values_keep_typography_and_manipulation_does_not() -> None:
    manifest = _phase2_manifest()
    reference = SYNTHETIC / "template_reference.pdf"
    legitimate = SYNTHETIC / "template_legitimate_candidate.pdf"
    manipulated = SYNTHETIC / "template_manipulated_candidate.pdf"
    for field in manifest["template"]["variable_fields"]:
        reference_span = _span(reference, field["reference"])
        legitimate_span = _span(legitimate, field["legitimate"])
        assert reference_span["font"] == legitimate_span["font"]
        assert reference_span["size"] == legitimate_span["size"]
        assert abs(reference_span["origin"][0] - legitimate_span["origin"][0]) < 0.01
        assert abs(reference_span["origin"][1] - legitimate_span["origin"][1]) < 0.01

    legitimate_result = _span(legitimate, "MERIT")
    manipulated_result = _span(manipulated, "MERIT")
    assert manipulated_result["font"] != legitimate_result["font"]
    assert manipulated_result["size"] > legitimate_result["size"]
    assert manipulated_result["color"] != legitimate_result["color"]

    legitimate_image = cv2.imread(str(SYNTHETIC / "template_legitimate_candidate_page_1.png"))
    manipulated_image = cv2.imread(str(SYNTHETIC / "template_manipulated_candidate_page_1.png"))
    mask = cv2.imread(
        str(SAMPLES / manifest["template"]["manipulated"]["mask"]),
        cv2.IMREAD_GRAYSCALE,
    )
    assert legitimate_image is not None and manipulated_image is not None and mask is not None
    changed = np.any(cv2.absdiff(legitimate_image, manipulated_image) >= 8, axis=2)
    assert np.count_nonzero(changed & (mask > 0)) / np.count_nonzero(changed) >= 0.95


def test_raster_pdf_has_one_image_and_zero_embedded_text() -> None:
    manifest = _phase2_manifest()["raster_ocr"]
    pdf_path = SAMPLES / manifest["pdf"]
    with fitz.open(pdf_path) as document:
        assert document.page_count == 1
        assert document[0].get_text("text").strip() == ""
        assert len(document[0].get_images(full=True)) == 1

    image = cv2.imread(str(SAMPLES / manifest["png"]), cv2.IMREAD_GRAYSCALE)
    assert image is not None and image.shape == (1800, 1272)
    assert len(manifest["expected_tokens"]) >= 8
    for expected in manifest["expected_tokens"]:
        box = expected["normalized_bbox"]
        assert 0 <= box["x"] < 1 and 0 <= box["y"] < 1
        assert 0 < box["width"] <= 1 and 0 < box["height"] <= 1
        x0 = round(box["x"] * image.shape[1])
        y0 = round(box["y"] * image.shape[0])
        x1 = round((box["x"] + box["width"]) * image.shape[1])
        y1 = round((box["y"] + box["height"]) * image.shape[0])
        crop = image[y0:y1, x0:x1]
        assert crop.size > 0 and float(crop.std()) > 4.0, expected["text"]


def test_raster_fixture_produces_expected_ocr_tokens_and_boxes() -> None:
    manifest = _phase2_manifest()["raster_ocr"]
    image = cv2.imread(str(SAMPLES / manifest["png"]), cv2.IMREAD_COLOR)
    assert image is not None
    provider = get_raster_ocr_provider()
    assert provider.available is True
    result = provider.extract(image)
    assert result.succeeded is True
    assert result.provider == "rapidocr_onnxruntime"
    assert result.device == "cpu"
    assert result.confidence is not None and result.confidence >= 0.80

    matched = 0
    for expected in manifest["expected_tokens"]:
        normalized_expected = re.sub(r"\W+", "", expected["text"]).casefold()
        candidates = [
            word
            for word in result.words
            if normalized_expected in re.sub(r"\W+", "", word.text).casefold()
        ]
        if not candidates:
            continue
        matched += 1
        assert max(
            _box_iou(expected["normalized_bbox"], word.bbox) for word in candidates
        ) >= manifest["approximate_box_minimum_iou"], expected["text"]
    assert matched >= manifest["minimum_token_matches"]


def test_forensics_runtime_does_not_read_expected_fixture_data() -> None:
    production_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((PROJECT_ROOT / "backend" / "app").rglob("*.py"))
    ).casefold()
    for forbidden in ("phase2_manifest", "manifest.json", "tamper_mask", "samples/expected"):
        assert forbidden not in production_source
