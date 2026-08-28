"""Pixel, edge, and text-assisted difference localization.

All returned regions are expressed in candidate-page pixels.  Reference text
boxes can be mapped through the actual reference-to-candidate homography, which
prevents evidence markers from drifting after alignment.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from backend.app.forensics.text import RegionRole, TextChange


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
    reference_to_candidate_matrix: np.ndarray | None = None,
    *,
    reference_size: tuple[int, int] | None = None,
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
    intensity_threshold = _adaptive_intensity_threshold(delta)
    intensity_mask = delta >= intensity_threshold
    edge_mask = (edge_delta >= 200) & (raw_delta >= 8)
    mask = ((intensity_mask | edge_mask).astype(np.uint8) * 255)
    height, width = mask.shape
    reference_width, reference_height = reference_size or (
        aligned_reference.shape[1],
        aligned_reference.shape[0],
    )
    border = max(3, round(min(width, height) * 0.004))
    mask[:border, :] = 0
    mask[-border:, :] = 0
    mask[:, :border] = 0
    mask[:, -border:] = 0
    close_width = max(5, round(width * 0.0055))
    if close_width % 2 == 0:
        close_width += 1
    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (close_width, 3)),
        iterations=2,
    )
    mask = cv2.dilate(
        mask,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, close_width // 2), 3)),
        iterations=1,
    )

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
        padding = max(4, round(min(width, height) * 0.004))
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
        text_region = _text_region(
            change,
            width,
            height,
            reference_to_candidate_matrix=reference_to_candidate_matrix,
            reference_width=reference_width,
            reference_height=reference_height,
        )
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
    regions.sort(
        key=lambda region: (bool(region.text_changes), region.changed_pixels), reverse=True
    )
    changed_pixels = int(np.count_nonzero(filtered_mask))
    return DifferenceResult(
        mask=filtered_mask,
        intensity_delta=raw_delta,
        regions=regions[:12],
        global_changed_ratio=changed_pixels / float(width * height),
        global_mean_delta=float(raw_delta.mean()),
    )


def _text_region(
    change: TextChange,
    width: int,
    height: int,
    *,
    reference_to_candidate_matrix: np.ndarray | None,
    reference_width: int,
    reference_height: int,
) -> DifferenceRegion:
    boxes: list[tuple[float, float, float, float]] = []
    if change.candidate_bbox is not None:
        boxes.append(_normalized_to_pixels(change.candidate_bbox, width, height))
    if change.reference_bbox is not None:
        boxes.append(
            _map_reference_box(
                change.reference_bbox,
                reference_width,
                reference_height,
                width,
                height,
                reference_to_candidate_matrix,
            )
        )
    if not boxes:
        boxes.append(_normalized_to_pixels(change.bbox, width, height))

    x0 = min(box[0] for box in boxes)
    y0 = min(box[1] for box in boxes)
    x1 = max(box[2] for box in boxes)
    y1 = max(box[3] for box in boxes)

    # One line-height of context makes evidence crops readable and keeps the
    # marker stable when replacement strings have very different lengths.  The
    # caps prevent a large heading from swallowing an unrelated page region.
    line_height = max(1.0, y1 - y0)
    pad_x = max(7, round(min(width * 0.035, line_height)))
    pad_y = max(5, round(min(height * 0.028, line_height)))
    sources = {"embedded_text_change"}
    if change.role is RegionRole.VARIABLE:
        sources.add("variable_region_suggestion")
    elif change.role is RegionRole.FIXED:
        sources.add("fixed_region_change")
    return DifferenceRegion(
        x0=max(0, round(x0) - pad_x),
        y0=max(0, round(y0) - pad_y),
        x1=min(width, round(x1) + pad_x),
        y1=min(height, round(y1) + pad_y),
        evidence_sources=sources,
        text_changes=[change],
    )


def _normalized_to_pixels(
    bbox: tuple[float, float, float, float], width: int, height: int
) -> tuple[float, float, float, float]:
    return bbox[0] * width, bbox[1] * height, bbox[2] * width, bbox[3] * height


def _map_reference_box(
    bbox: tuple[float, float, float, float],
    reference_width: int,
    reference_height: int,
    width: int,
    height: int,
    matrix: np.ndarray | None,
) -> tuple[float, float, float, float]:
    pixel_box = _normalized_to_pixels(bbox, reference_width, reference_height)
    if matrix is None:
        return pixel_box
    corners = np.float32(
        [
            [pixel_box[0], pixel_box[1]],
            [pixel_box[2], pixel_box[1]],
            [pixel_box[2], pixel_box[3]],
            [pixel_box[0], pixel_box[3]],
        ]
    ).reshape(-1, 1, 2)
    mapped = cv2.perspectiveTransform(corners, matrix).reshape(-1, 2)
    if not np.isfinite(mapped).all():
        return pixel_box
    return (
        float(np.clip(mapped[:, 0].min(), 0, width)),
        float(np.clip(mapped[:, 1].min(), 0, height)),
        float(np.clip(mapped[:, 0].max(), 0, width)),
        float(np.clip(mapped[:, 1].max(), 0, height)),
    )


def _adaptive_intensity_threshold(delta: np.ndarray) -> int:
    """Choose a noise-aware threshold without using fixture-specific values."""

    median = float(np.median(delta))
    deviation = float(np.median(np.abs(delta.astype(np.float32) - median)))
    return int(round(np.clip(median + 5.5 * deviation, 20.0, 44.0)))


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
    # A newly enlarged destination can bridge two earlier components.  Collapse
    # those transitive neighbors so one visual change yields one evidence marker.
    changed = True
    while changed:
        changed = False
        for first_index, first in enumerate(merged):
            for second_index in range(first_index + 1, len(merged)):
                second = merged[second_index]
                x_gap = max(0, first.x0 - second.x1, second.x0 - first.x1)
                y_gap = max(0, first.y0 - second.y1, second.y0 - first.y1)
                vertical_overlap = min(first.y1, second.y1) - max(first.y0, second.y0)
                horizontal_overlap = min(first.x1, second.x1) - max(first.x0, second.x0)
                if (
                    (vertical_overlap > 0 and x_gap <= horizontal_gap)
                    or (horizontal_overlap > 0 and y_gap <= vertical_gap)
                    or (x_gap <= 5 and y_gap <= 5)
                ):
                    _merge_into(first, second)
                    del merged[second_index]
                    changed = True
                    break
            if changed:
                break
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
