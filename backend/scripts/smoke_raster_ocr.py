"""Run a local cold/warm raster-OCR smoke on the deterministic fixture."""

from __future__ import annotations

import ctypes
import json
import os
import time
from pathlib import Path

import fitz

from backend.app.services.documents import (
    extract_page_text,
    render_document_page,
    validate_upload,
)
from backend.app.services.ocr import get_raster_ocr_provider


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLES = PROJECT_ROOT / "samples"
MANIFEST = json.loads(
    (SAMPLES / "expected" / "phase2_manifest.json").read_text(encoding="utf-8")
)


def _working_set_mib() -> float | None:
    """Return this process's resident working set without an extra dependency."""
    if os.name != "nt":
        return None

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("page_fault_count", ctypes.c_ulong),
            ("peak_working_set_size", ctypes.c_size_t),
            ("working_set_size", ctypes.c_size_t),
            ("quota_peak_paged_pool_usage", ctypes.c_size_t),
            ("quota_paged_pool_usage", ctypes.c_size_t),
            ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
            ("quota_non_paged_pool_usage", ctypes.c_size_t),
            ("pagefile_usage", ctypes.c_size_t),
            ("peak_pagefile_usage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(ProcessMemoryCounters),
        ctypes.c_ulong,
    )
    psapi.GetProcessMemoryInfo.restype = ctypes.c_bool
    process = kernel32.GetCurrentProcess()
    if not psapi.GetProcessMemoryInfo(
        process, ctypes.byref(counters), counters.cb
    ):
        return None
    return round(counters.working_set_size / (1024 * 1024), 2)


def _iou(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    x0 = max(left[0], right[0])
    y0 = max(left[1], right[1])
    x1 = min(left[2], right[2])
    y1 = min(left[3], right[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0


def main() -> None:
    fixture = SAMPLES / MANIFEST["raster_ocr"]["pdf"]
    with fitz.open(fixture) as document:
        embedded_text_characters = len(document[0].get_text("text").strip())
    if embedded_text_characters:
        raise RuntimeError("raster fixture unexpectedly contains embedded PDF text")
    upload = validate_upload(
        field="candidate",
        filename=fixture.name,
        content_type="application/pdf",
        data=fixture.read_bytes(),
        max_bytes=20 * 1024 * 1024,
    )
    rendered = render_document_page(upload, 0, 1800)
    provider = get_raster_ocr_provider("rapidocr")
    ram_before = _working_set_mib()

    cold_started = time.perf_counter()
    cold = extract_page_text(
        upload, rendered, 0, ocr_provider_preference="rapidocr"
    )
    cold_seconds = time.perf_counter() - cold_started
    ram_after_cold = _working_set_mib()
    warm_started = time.perf_counter()
    warm = extract_page_text(
        upload, rendered, 0, ocr_provider_preference="rapidocr"
    )
    warm_seconds = time.perf_counter() - warm_started
    ram_after_warm = _working_set_mib()

    expected = MANIFEST["raster_ocr"]["expected_tokens"]
    minimum_box_iou = MANIFEST["raster_ocr"]["approximate_box_minimum_iou"]
    matched_box_ious: dict[str, float] = {}
    for item in expected:
        token = item["text"].casefold()
        candidates = [word for word in warm.words if token in word.text.casefold()]
        if not candidates:
            continue
        box = item["normalized_bbox"]
        expected_xyxy = (
            box["x"],
            box["y"],
            box["x"] + box["width"],
            box["y"] + box["height"],
        )
        best_iou = max(_iou(expected_xyxy, word.bbox) for word in candidates)
        if best_iou < minimum_box_iou:
            raise RuntimeError(
                f"raster OCR box for {item['text']} missed the IoU threshold"
            )
        matched_box_ious[item["text"]] = round(best_iou, 4)
    matches = len(matched_box_ious)
    normalized_boxes = bool(warm.words) and all(
        0 <= word.bbox[0] <= word.bbox[2] <= 1
        and 0 <= word.bbox[1] <= word.bbox[3] <= 1
        for word in warm.words
    )
    if not cold.succeeded or not warm.succeeded:
        raise RuntimeError(cold.error or warm.error or "raster OCR failed")
    if matches < MANIFEST["raster_ocr"]["minimum_token_matches"]:
        raise RuntimeError("raster OCR returned too few expected fixture tokens")
    if not normalized_boxes:
        raise RuntimeError("raster OCR returned a non-normalized bounding box")
    if provider.initialization_count != 1:
        raise RuntimeError("raster OCR provider was initialized more than once")

    print(
        json.dumps(
            {
                "provider": warm.source,
                "device": warm.device,
                "embedded_text_characters": embedded_text_characters,
                "confidence": warm.confidence,
                "word_count": len(warm.words),
                "expected_token_matches": matches,
                "normalized_boxes": normalized_boxes,
                "minimum_box_iou": minimum_box_iou,
                "matched_box_ious": matched_box_ious,
                "cold_seconds": round(cold_seconds, 3),
                "warm_seconds": round(warm_seconds, 3),
                "initialization_count": provider.initialization_count,
                "model_reused": True,
                "working_set_mib": {
                    "before": ram_before,
                    "after_cold": ram_after_cold,
                    "after_warm": ram_after_warm,
                    "cold_delta": (
                        round(ram_after_cold - ram_before, 2)
                        if ram_before is not None and ram_after_cold is not None
                        else None
                    ),
                    "warm_delta": (
                        round(ram_after_warm - ram_after_cold, 2)
                        if ram_after_cold is not None and ram_after_warm is not None
                        else None
                    ),
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
