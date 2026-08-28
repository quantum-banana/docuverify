"""Pixel/edge difference localization with conservative component filtering."""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from backend.app.forensics.text import TextChange


@dataclass(slots=True)
class DifferenceRegion:
    x0: int
    y0: int
    x1: int
    y1: int
    changed_pixels: int = 0
    mean_delta: float = 0.0
    max_delta: int = 0
    edge_changed_pixels: int = 0
    evidence_sources: set[str] = field(default_factory=set)
    text_changes: list[TextChange] = field(default_factory=list)

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0


@dataclass(slots=True)
class DifferenceResult:
    mask: np.ndarray
    intensity_delta: np.ndarray
    regions: list[DifferenceRegion]
    global_changed_ratio: float
    global_mean_delta: float


def localize_differences(
    aligned_reference: np.ndarray,
    candidate: np.ndarray,
    text_changes: tuple[TextChange, ...],
) -> DifferenceResult:
    gray_reference = cv2.cvtColor(aligned_reference, cv2.COLOR_BGR2GRAY)
    gray_candidate = cv2.cvtColor(candidate, cv2.COLOR_BGR2GRAY)
    smooth_reference = cv2.GaussianBlur(gray_reference, (3, 3), 0)
    smooth_candidate = cv2.GaussianBlur(gray_candidate, (3, 3), 0)
    delta = cv2.absdiff(smooth_reference, smooth_candidate)
    raw_delta = cv2.absdiff(gray_reference, gray_candidate)
    edge_reference = cv2.Canny(gray_reference, 70, 150)
    edge_candidate = cv2.Canny(gray_candidate, 70, 150)
    edge_delta = cv2.absdiff(edge_reference, edge_candidate)
    intensity_mask = delta >= 24
    edge_mask = (edge_delta >= 200) & (raw_delta >= 8)
    mask = ((intensity_mask | edge_mask).astype(np.uint8) * 255)
    height, width = mask.shape
    border = max(3, round(min(width, height) * 0.004))
    mask[:border, :] = 0
    mask[-border:, :] = 0
    mask[:, :border] = 0
    mask[:, -border:] = 0
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (7, 3)), iterations=2
    )
    mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3)), iterations=1)

    minimum_area = max(45, round(width * height * 0.000012))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    component_regions: list[DifferenceRegion] = []
    filtered_mask = np.zeros_like(mask)
    for label in range(1, count):
        x, y, component_width, component_height, area = stats[label]
        if area < minimum_area or component_width < 3 or component_height < 3:
            continue
        component = labels == label
        component_delta = raw_delta[component]
        if component_delta.size == 0 or float(component_delta.mean()) < 12.0:
            continue
        filtered_mask[component] = 255
        padding = max(6, round(min(width, height) * 0.006))
        x0, y0 = max(0, x - padding), max(0, y - padding)
        x1 = min(width, x + component_width + padding)
        y1 = min(height, y + component_height + padding)
        component_regions.append(
            DifferenceRegion(
                x0=x0,
                y0=y0,
                x1=x1,
                y1=y1,
                changed_pixels=int(np.count_nonzero(component)),
                mean_delta=float(component_delta.mean()),
                max_delta=int(component_delta.max()),
                edge_changed_pixels=int(np.count_nonzero(edge_delta[component])),
                evidence_sources={"visual_difference"},
            )
        )

    regions = _merge_nearby(component_regions, width, height)
    for change in text_changes:
        text_region = _text_region(change, width, height)
        matching = next((region for region in regions if _overlaps(region, text_region)), None)
        if matching is None:
            regions.append(text_region)
        else:
            _merge_into(matching, text_region)
    regions = _merge_nearby(regions, width, height)
    for region in regions:
        region_slice = np.s_[region.y0 : region.y1, region.x0 : region.x1]
        changed = filtered_mask[region_slice] > 0
        if np.any(changed):
            values = raw_delta[region_slice][changed]
            region.changed_pixels = max(region.changed_pixels, int(changed.sum()))
            region.mean_delta = max(region.mean_delta, float(values.mean()))
            region.max_delta = max(region.max_delta, int(values.max()))
            region.edge_changed_pixels = max(
                region.edge_changed_pixels, int(np.count_nonzero(edge_delta[region_slice][changed]))
            )
    regions.sort(key=lambda region: (bool(region.text_changes), region.changed_pixels), reverse=True)
    changed_pixels = int(np.count_nonzero(filtered_mask))
    return DifferenceResult(
        mask=filtered_mask,
        intensity_delta=raw_delta,
        regions=regions[:8],
        global_changed_ratio=changed_pixels / float(width * height),
        global_mean_delta=float(raw_delta.mean()),
    )


def _text_region(change: TextChange, width: int, height: int) -> DifferenceRegion:
    x0, y0, x1, y1 = change.bbox
    pad_x, pad_y = max(7, round(width * 0.008)), max(5, round(height * 0.005))
    return DifferenceRegion(
        x0=max(0, round(x0 * width) - pad_x),
        y0=max(0, round(y0 * height) - pad_y),
        x1=min(width, round(x1 * width) + pad_x),
        y1=min(height, round(y1 * height) + pad_y),
        evidence_sources={"embedded_text_change"},
        text_changes=[change],
    )


def _overlaps(first: DifferenceRegion, second: DifferenceRegion) -> bool:
    pad = 20
    return not (
        first.x1 + pad < second.x0
        or second.x1 + pad < first.x0
        or first.y1 + pad < second.y0
        or second.y1 + pad < first.y0
    )


def _merge_nearby(
    regions: list[DifferenceRegion], width: int, height: int
) -> list[DifferenceRegion]:
    merged: list[DifferenceRegion] = []
    horizontal_gap = max(14, round(width * 0.012))
    vertical_gap = max(8, round(height * 0.007))
    for source in sorted(regions, key=lambda region: (region.y0, region.x0)):
        destination = None
        for candidate in merged:
            x_gap = max(0, source.x0 - candidate.x1, candidate.x0 - source.x1)
            y_gap = max(0, source.y0 - candidate.y1, candidate.y0 - source.y1)
            vertical_overlap = min(source.y1, candidate.y1) - max(source.y0, candidate.y0)
            horizontal_overlap = min(source.x1, candidate.x1) - max(source.x0, candidate.x0)
            same_line = vertical_overlap > 0 and x_gap <= horizontal_gap
            same_column = horizontal_overlap > 0 and y_gap <= vertical_gap
            if same_line or same_column or (x_gap <= 5 and y_gap <= 5):
                destination = candidate
                break
        if destination is None:
            merged.append(source)
        else:
            _merge_into(destination, source)
    return merged


def _merge_into(destination: DifferenceRegion, source: DifferenceRegion) -> None:
    total_pixels = destination.changed_pixels + source.changed_pixels
    if total_pixels:
        destination.mean_delta = (
            destination.mean_delta * destination.changed_pixels
            + source.mean_delta * source.changed_pixels
        ) / total_pixels
    destination.changed_pixels = total_pixels
    destination.max_delta = max(destination.max_delta, source.max_delta)
    destination.edge_changed_pixels += source.edge_changed_pixels
    destination.x0 = min(destination.x0, source.x0)
    destination.y0 = min(destination.y0, source.y0)
    destination.x1 = max(destination.x1, source.x1)
    destination.y1 = max(destination.y1, source.y1)
    destination.evidence_sources.update(source.evidence_sources)
    destination.text_changes.extend(source.text_changes)
