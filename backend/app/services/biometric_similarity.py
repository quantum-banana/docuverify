"""Explainable local handwriting and signature appearance comparison.

The feature ensemble is intentionally classical and CPU-safe. Scores express
appearance consistency with supplied exemplars, never legal identity or
authorship proof.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Sequence

import cv2
import numpy as np

from backend.app.docuvault.repository import DocumentProfile
from backend.app.models.contracts import (
    BoundingBox,
    CheckStatus,
    SimilarityAssessment,
    SimilarityRegionEvidence,
)
from backend.app.services.documents import ValidatedUpload, render_document_page


BiometricKind = Literal["handwriting", "signature"]


@dataclass(frozen=True, slots=True)
class RegionSelection:
    page_number: int
    bounding_box: BoundingBox


@dataclass(slots=True)
class _Features:
    mask: np.ndarray
    skeleton: np.ndarray
    hog: np.ndarray
    texture: np.ndarray
    projection: np.ndarray
    hu: np.ndarray
    structure: np.ndarray
    orb: np.ndarray | None
    quality: float


@dataclass(frozen=True, slots=True)
class _CandidateRegion:
    page_number: int
    bounding_box: BoundingBox
    crop: np.ndarray
    page_image: np.ndarray
    source: Literal["profile", "user", "suggested"]


def compare_biometric_regions(
    *,
    kind: BiometricKind,
    candidate_pages: Sequence[Any],
    exemplars: Sequence[ValidatedUpload],
    profile: DocumentProfile | None,
    user_regions: Sequence[RegionSelection] = (),
    max_render_dimension: int = 1800,
) -> SimilarityAssessment:
    minimum_exemplars = 2 if kind == "signature" else 1
    if not exemplars:
        return SimilarityAssessment(
            status=CheckStatus.NOT_APPLICABLE,
            explanation=f"No trusted {kind} exemplars were supplied.",
            limitations=[_identity_limitation(kind)],
        )
    if len(exemplars) < minimum_exemplars:
        return SimilarityAssessment(
            status=CheckStatus.SKIPPED,
            explanation=f"At least {minimum_exemplars} trusted {kind} exemplar(s) are required.",
            limitations=[_identity_limitation(kind)],
        )

    exemplar_features: list[tuple[str, _Features]] = []
    for exemplar_index, upload in enumerate(exemplars, start=1):
        for page_index in range(int(upload.page_count)):
            rendered = render_document_page(upload, page_index, max_render_dimension)
            height, width = rendered.image.shape[:2]
            looks_like_cropped_sample = width / max(height, 1) >= 1.6
            suggested = (
                None
                if looks_like_cropped_sample
                else _suggest_region(rendered.image, kind)
            )
            crop = (
                _crop(rendered.image, suggested)
                if suggested is not None
                else _trim_to_ink(rendered.image, kind)
            )
            features = _features(crop, kind)
            if features.quality >= 0.18:
                exemplar_features.append((f"exemplar_{exemplar_index}", features))
    if len({name for name, _ in exemplar_features}) < minimum_exemplars:
        return SimilarityAssessment(
            status=CheckStatus.SKIPPED,
            explanation=f"Too few {kind} exemplars contained sufficient foreground detail.",
            limitations=[
                _identity_limitation(kind),
                "Low-quality or blank samples are excluded rather than treated as mismatches.",
            ],
        )

    candidate_regions = _candidate_regions(
        kind=kind,
        pages=candidate_pages,
        profile=profile,
        user_regions=user_regions,
    )
    if not candidate_regions:
        return SimilarityAssessment(
            status=CheckStatus.SKIPPED,
            explanation=f"No reliable {kind} region could be selected from the questioned document.",
            limitations=[
                _identity_limitation(kind),
                "A profile-defined or user-selected region can improve coverage.",
            ],
        )

    evidence: list[SimilarityRegionEvidence] = []
    closest_names: list[str] = []
    best_scores: list[float] = []
    confidence_values: list[float] = []
    compositing_values: list[float] = []
    position_values: list[float] = []
    scale_values: list[float] = []
    aggregate_measurements: list[dict[str, float]] = []
    for region in candidate_regions:
        candidate_features = _features(region.crop, kind)
        if candidate_features.quality < 0.18:
            continue
        comparisons = [
            (name, *_similarity(candidate_features, trusted))
            for name, trusted in exemplar_features
        ]
        comparisons.sort(key=lambda item: (-item[1], item[0]))
        closest, best, measurements = comparisons[0]
        second = comparisons[1][1] if len(comparisons) > 1 else best
        confidence = min(
            97.0,
            48.0
            + 22.0 * candidate_features.quality
            + 8.0 * min(1.0, len({name for name, _ in exemplar_features}) / 3)
            + 12.0 * abs(best - second),
        )
        compositing = (
            _compositing_score(region.page_image, region.bounding_box)
            if kind == "signature"
            else 0.0
        )
        position_anomaly, scale_anomaly = (
            _placement_anomalies(region.crop)
            if kind == "signature" and region.source == "profile"
            else (0.0, 0.0)
        )
        score = round(best * 100.0, 1)
        explanation = _region_explanation(kind, score, measurements, compositing)
        evidence.append(
            SimilarityRegionEvidence(
                page_number=region.page_number,
                bounding_box=region.bounding_box,
                similarity_score=score,
                confidence_score=round(confidence, 1),
                measurements={
                    **{name: round(value * 100.0, 1) for name, value in measurements.items()},
                    "sample_quality": round(candidate_features.quality * 100.0, 1),
                    "compositing_score": round(compositing, 1),
                    "position_anomaly": round(position_anomaly, 1),
                    "scale_anomaly": round(scale_anomaly, 1),
                    "region_source": region.source,
                },
                explanation=explanation,
            )
        )
        closest_names.append(closest)
        best_scores.append(score)
        confidence_values.append(confidence)
        compositing_values.append(compositing)
        position_values.append(position_anomaly)
        scale_values.append(scale_anomaly)
        aggregate_measurements.append(measurements)

    if not evidence:
        return SimilarityAssessment(
            status=CheckStatus.SKIPPED,
            explanation=f"Selected {kind} regions had insufficient stroke quality for comparison.",
            limitations=[_identity_limitation(kind)],
        )
    overall = round(float(np.mean(best_scores)), 1)
    confidence = round(float(np.mean(confidence_values)), 1)
    coverage = round(100.0 * len(evidence) / len(candidate_regions), 1)
    compositing = round(max(compositing_values), 1) if kind == "signature" else None
    position_anomaly = max(position_values, default=0.0)
    scale_anomaly = max(scale_values, default=0.0)
    threshold = 58.0 if kind == "handwriting" else 55.0
    if overall < threshold:
        status = CheckStatus.FAILED
        explanation = f"Questioned {kind} appearance differs materially from the supplied exemplar ensemble."
    elif overall < 72.0:
        status = CheckStatus.WARNING
        explanation = f"Questioned {kind} appearance has mixed similarity to the supplied exemplars."
    else:
        status = CheckStatus.PASSED
        explanation = f"Questioned {kind} appearance is broadly consistent with the supplied exemplar ensemble."
    if compositing is not None and compositing >= 65.0 and status is CheckStatus.PASSED:
        status = CheckStatus.WARNING
        explanation += " Independent boundary/background evidence suggests a possible pasted signature region."
    if (
        kind == "signature"
        and max(position_anomaly, scale_anomaly) >= 65.0
        and status is CheckStatus.PASSED
    ):
        status = CheckStatus.WARNING
        explanation += " Profile-region placement or occupied scale is atypical."
    reasons = _aggregate_reasons(kind, aggregate_measurements, overall, compositing)
    if kind == "signature" and max(position_anomaly, scale_anomaly) > 0:
        reasons.extend(
            [
                f"Profile-region position anomaly indicator: {position_anomaly:.1f}.",
                f"Profile-region scale anomaly indicator: {scale_anomaly:.1f}.",
            ]
        )
    closest = max(set(closest_names), key=lambda name: (closest_names.count(name), name))
    return SimilarityAssessment(
        status=status,
        similarity_score=overall,
        confidence_score=confidence,
        coverage_score=coverage,
        closest_exemplar=closest,
        region_evidence=evidence,
        reasons=reasons,
        compositing_score=compositing,
        explanation=explanation,
        limitations=[
            _identity_limitation(kind),
            "Document capture quality, pen, scale, writing conditions and natural variation can affect the score.",
            *(
                [
                    "A copied genuine signature can look similar; compositing and placement evidence are reported separately."
                ]
                if kind == "signature"
                else []
            ),
        ],
    )


def _candidate_regions(
    *,
    kind: BiometricKind,
    pages: Sequence[Any],
    profile: DocumentProfile | None,
    user_regions: Sequence[RegionSelection],
) -> list[_CandidateRegion]:
    selections: list[RegionSelection] = []
    source: Literal["profile", "user", "suggested"] = "suggested"
    if profile is not None:
        source = "profile"
        selections = [
            RegionSelection(
                int(region["page"]),
                BoundingBox.model_validate(region["box"]),
            )
            for region in profile.manifest["security_regions"][kind]
        ]
    if not selections and user_regions:
        source = "user"
        selections = list(user_regions)
    images: dict[int, np.ndarray] = {}
    regions: list[_CandidateRegion] = []
    for selection in selections:
        if not 1 <= selection.page_number <= len(pages):
            continue
        image = images.setdefault(
            selection.page_number,
            cv2.imread(str(pages[selection.page_number - 1].image_path), cv2.IMREAD_COLOR),
        )
        if image is None:
            continue
        crop = _crop(image, selection.bounding_box)
        if crop.size:
            regions.append(
                _CandidateRegion(
                    selection.page_number,
                    selection.bounding_box,
                    crop,
                    image,
                    source,
                )
            )
    if regions:
        return regions[:10]

    for page_number, page in enumerate(pages, start=1):
        image = cv2.imread(str(page.image_path), cv2.IMREAD_COLOR)
        if image is None:
            continue
        suggested = _suggest_region(image, kind)
        if suggested is not None:
            regions.append(
                _CandidateRegion(
                    page_number,
                    suggested,
                    _crop(image, suggested),
                    image,
                    "suggested",
                )
            )
    return regions[:10]


def _suggest_region(image: np.ndarray, kind: BiometricKind) -> BoundingBox | None:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    height, width = ink.shape
    start_y = int(height * (0.42 if kind == "handwriting" else 0.55))
    search = ink[start_y:]
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(5, width // 80), max(3, height // 240)))
    grouped = cv2.morphologyEx(search, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _ = cv2.findContours(grouped, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[tuple[float, tuple[int, int, int, int]]] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        y += start_y
        area_ratio = w * h / max(width * height, 1)
        aspect = w / max(h, 1)
        if kind == "signature":
            plausible = 0.001 <= area_ratio <= 0.16 and 1.2 <= aspect <= 12
        else:
            plausible = 0.002 <= area_ratio <= 0.35 and 1.5 <= aspect <= 20
        if plausible:
            score = area_ratio * (1.0 + y / height) * min(aspect, 6)
            candidates.append((score, (x, y, w, h)))
    if not candidates:
        return None
    _, (x, y, w, h) = max(candidates, key=lambda item: item[0])
    pad_x, pad_y = max(4, round(w * 0.12)), max(4, round(h * 0.3))
    x0, y0 = max(0, x - pad_x), max(0, y - pad_y)
    x1, y1 = min(width, x + w + pad_x), min(height, y + h + pad_y)
    return BoundingBox(
        x=x0 / width,
        y=y0 / height,
        width=max(1, x1 - x0) / width,
        height=max(1, y1 - y0) / height,
    )


def _crop(image: np.ndarray, box: BoundingBox) -> np.ndarray:
    height, width = image.shape[:2]
    x0, y0 = int(box.x * width), int(box.y * height)
    x1 = min(width, max(x0 + 1, int((box.x + box.width) * width)))
    y1 = min(height, max(y0 + 1, int((box.y + box.height) * height)))
    return image[y0:y1, x0:x1].copy()


def _trim_to_ink(image: np.ndarray, kind: BiometricKind) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    points = cv2.findNonZero(mask)
    if points is None:
        return image
    x, y, w, h = cv2.boundingRect(points)
    pad = max(4, round(min(w, h) * (0.12 if kind == "signature" else 0.08)))
    return image[max(0, y - pad) : min(image.shape[0], y + h + pad), max(0, x - pad) : min(image.shape[1], x + w + pad)]


def _features(image: np.ndarray, kind: BiometricKind) -> _Features:
    target = (320, 160) if kind == "signature" else (384, 192)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image.copy()
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    _, raw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    raw = cv2.morphologyEx(raw, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    gray, raw = _conservative_deskew(gray, raw)
    points = cv2.findNonZero(raw)
    if points is not None:
        x, y, w, h = cv2.boundingRect(points)
        raw = raw[max(0, y - 2) : min(raw.shape[0], y + h + 2), max(0, x - 2) : min(raw.shape[1], x + w + 2)]
        gray = gray[max(0, y - 2) : min(gray.shape[0], y + h + 2), max(0, x - 2) : min(gray.shape[1], x + w + 2)]
    mask = _fit_canvas(raw, target)
    normal_gray = _fit_canvas(255 - gray, target)
    ink_ratio = float(np.mean(mask > 0))
    components, _, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), 8)
    component_stats = stats[1:] if components > 1 else np.empty((0, 5))
    foreground_pixels = int(np.count_nonzero(mask))
    quality = min(
        1.0,
        0.55 * min(1.0, ink_ratio / 0.025)
        + 0.25 * min(1.0, max(0, components - 1) / 4)
        + 0.20 * min(1.0, foreground_pixels / 500),
    )
    skeleton = _skeleton(mask)
    hog = _hog(mask)
    texture = _lbp_histogram(normal_gray)
    row = np.mean(mask > 0, axis=1)
    column = np.mean(mask > 0, axis=0)
    projection = np.concatenate((row, column)).astype(np.float32)
    moments = cv2.HuMoments(cv2.moments(mask)).flatten()
    hu = (-np.sign(moments) * np.log10(np.abs(moments) + 1e-12)).astype(np.float32)
    structure = _structural_features(mask, skeleton, component_stats)
    orb = _orb_descriptors(mask)
    return _Features(mask, skeleton, hog, texture, projection, hu, structure, orb, quality)


def _conservative_deskew(
    gray: np.ndarray, mask: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    points = cv2.findNonZero(mask)
    if points is None or len(points) < 24:
        return gray, mask
    line = cv2.fitLine(points, cv2.DIST_L2, 0, 0.01, 0.01).reshape(-1)
    vx, vy = float(line[0]), float(line[1])
    angle = float(np.degrees(np.arctan2(vy, vx)))
    if angle > 90:
        angle -= 180
    if angle < -90:
        angle += 180
    # Correct only small capture rotation. Larger slant remains style evidence.
    if abs(angle) < 0.35 or abs(angle) > 7.0:
        return gray, mask
    center = (gray.shape[1] / 2.0, gray.shape[0] / 2.0)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated_gray = cv2.warpAffine(
        gray,
        matrix,
        (gray.shape[1], gray.shape[0]),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=255,
    )
    rotated_mask = cv2.warpAffine(
        mask,
        matrix,
        (mask.shape[1], mask.shape[0]),
        flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    return rotated_gray, rotated_mask


def _fit_canvas(image: np.ndarray, target: tuple[int, int]) -> np.ndarray:
    width, height = target
    if image.size == 0:
        return np.zeros((height, width), dtype=np.uint8)
    scale = min((width - 8) / max(image.shape[1], 1), (height - 8) / max(image.shape[0], 1))
    resized = cv2.resize(
        image,
        (max(1, round(image.shape[1] * scale)), max(1, round(image.shape[0] * scale))),
        interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC,
    )
    canvas = np.zeros((height, width), dtype=np.uint8)
    y = (height - resized.shape[0]) // 2
    x = (width - resized.shape[1]) // 2
    canvas[y : y + resized.shape[0], x : x + resized.shape[1]] = resized
    return canvas


def _skeleton(mask: np.ndarray) -> np.ndarray:
    image = (mask > 0).astype(np.uint8) * 255
    skeleton = np.zeros_like(image)
    element = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    for _ in range(256):
        eroded = cv2.erode(image, element)
        opened = cv2.dilate(eroded, element)
        skeleton = cv2.bitwise_or(skeleton, cv2.subtract(image, opened))
        image = eroded
        if cv2.countNonZero(image) == 0:
            break
    return skeleton


def _hog(mask: np.ndarray) -> np.ndarray:
    image = mask.astype(np.float32) / 255.0
    gx = cv2.Sobel(image, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(image, cv2.CV_32F, 0, 1, ksize=3)
    magnitude, angle = cv2.cartToPolar(gx, gy, angleInDegrees=True)
    bins = (angle % 180 / 20).astype(np.int32)
    features: list[float] = []
    for y0 in range(0, image.shape[0], 24):
        for x0 in range(0, image.shape[1], 24):
            cell_bins = bins[y0 : y0 + 24, x0 : x0 + 24]
            cell_magnitude = magnitude[y0 : y0 + 24, x0 : x0 + 24]
            histogram = np.bincount(
                cell_bins.reshape(-1), weights=cell_magnitude.reshape(-1), minlength=9
            )[:9]
            histogram = histogram / max(float(np.linalg.norm(histogram)), 1e-6)
            features.extend(histogram.tolist())
    return np.asarray(features, dtype=np.float32)


def _lbp_histogram(gray: np.ndarray) -> np.ndarray:
    center = gray[1:-1, 1:-1]
    code = np.zeros_like(center, dtype=np.uint8)
    neighbours = (
        gray[:-2, :-2], gray[:-2, 1:-1], gray[:-2, 2:], gray[1:-1, 2:],
        gray[2:, 2:], gray[2:, 1:-1], gray[2:, :-2], gray[1:-1, :-2],
    )
    for bit, neighbour in enumerate(neighbours):
        code |= ((neighbour >= center).astype(np.uint8) << bit)
    histogram = cv2.calcHist([code], [0], None, [32], [0, 256]).reshape(-1)
    return (histogram / max(float(histogram.sum()), 1.0)).astype(np.float32)


def _structural_features(mask: np.ndarray, skeleton: np.ndarray, stats: np.ndarray) -> np.ndarray:
    ink = mask > 0
    ys, xs = np.nonzero(ink)
    if not len(xs):
        return np.zeros(12, dtype=np.float32)
    bottom_by_column = [np.max(np.nonzero(ink[:, x])[0]) for x in range(ink.shape[1]) if np.any(ink[:, x])]
    component_widths = stats[:, cv2.CC_STAT_WIDTH] if len(stats) else np.array([0])
    component_heights = stats[:, cv2.CC_STAT_HEIGHT] if len(stats) else np.array([0])
    gx = cv2.Sobel(mask.astype(np.float32), cv2.CV_32F, 1, 0)
    gy = cv2.Sobel(mask.astype(np.float32), cv2.CV_32F, 0, 1)
    angles = np.arctan2(gy, gx)
    weighted = angles[np.hypot(gx, gy) > 0.05]
    return np.asarray(
        [
            np.mean(ink),
            np.mean(skeleton > 0),
            len(stats) / 100.0,
            np.mean(component_widths) / mask.shape[1],
            np.std(component_widths) / mask.shape[1],
            np.mean(component_heights) / mask.shape[0],
            np.std(component_heights) / mask.shape[0],
            np.std(bottom_by_column) / mask.shape[0],
            np.mean(xs) / mask.shape[1],
            np.mean(ys) / mask.shape[0],
            float(np.mean(np.sin(weighted))) if weighted.size else 0.0,
            float(np.mean(np.cos(weighted))) if weighted.size else 0.0,
        ],
        dtype=np.float32,
    )


def _orb_descriptors(mask: np.ndarray) -> np.ndarray | None:
    detector = cv2.ORB_create(nfeatures=320, edgeThreshold=8, fastThreshold=8)
    _, descriptors = detector.detectAndCompute(mask, None)
    return descriptors


def _similarity(first: _Features, second: _Features) -> tuple[float, dict[str, float]]:
    measurements = {
        "gradient_hog": _cosine(first.hog, second.hog),
        "texture": float(np.minimum(first.texture, second.texture).sum()),
        "projection_spacing": _cosine(first.projection, second.projection),
        "contour_hu": float(np.exp(-np.mean(np.abs(first.hu - second.hu)) / 4.0)),
        "stroke_structure": float(np.exp(-np.mean(np.abs(first.structure - second.structure)) * 8.0)),
        "skeleton": _binary_iou(first.skeleton > 0, second.skeleton > 0),
        "keypoints": _orb_similarity(first.orb, second.orb),
    }
    weights = {
        "gradient_hog": 0.24,
        "texture": 0.06,
        "projection_spacing": 0.12,
        "contour_hu": 0.08,
        "stroke_structure": 0.16,
        "skeleton": 0.20,
        "keypoints": 0.14,
    }
    score = sum(measurements[name] * weight for name, weight in weights.items())
    return max(0.0, min(1.0, score)), measurements


def _cosine(first: np.ndarray, second: np.ndarray) -> float:
    if first.shape != second.shape:
        size = min(first.size, second.size)
        first, second = first[:size], second[:size]
    denominator = float(np.linalg.norm(first) * np.linalg.norm(second))
    if denominator <= 1e-8:
        return 0.0
    return max(0.0, min(1.0, float(np.dot(first, second) / denominator)))


def _binary_iou(first: np.ndarray, second: np.ndarray) -> float:
    intersection = np.count_nonzero(first & second)
    union = np.count_nonzero(first | second)
    return float(intersection / max(union, 1))


def _orb_similarity(first: np.ndarray | None, second: np.ndarray | None) -> float:
    if first is None or second is None or not len(first) or not len(second):
        return 0.35
    matches = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(first, second, k=2)
    good = [pair[0] for pair in matches if len(pair) == 2 and pair[0].distance < 0.78 * pair[1].distance]
    return min(1.0, len(good) / max(8.0, min(len(first), len(second)) * 0.35))


def _compositing_score(page: np.ndarray, box: BoundingBox) -> float:
    height, width = page.shape[:2]
    x0, y0 = int(box.x * width), int(box.y * height)
    x1, y1 = min(width, int((box.x + box.width) * width)), min(height, int((box.y + box.height) * height))
    margin = max(4, round(min(x1 - x0, y1 - y0) * 0.12))
    outer_x0, outer_y0 = max(0, x0 - margin), max(0, y0 - margin)
    outer_x1, outer_y1 = min(width, x1 + margin), min(height, y1 + margin)
    inner = page[y0:y1, x0:x1]
    outer = page[outer_y0:outer_y1, outer_x0:outer_x1]
    if inner.size == 0 or outer.size == 0 or min(inner.shape[:2]) < 4:
        return 0.0
    inner_gray = cv2.cvtColor(inner, cv2.COLOR_BGR2GRAY)
    outer_gray = cv2.cvtColor(outer, cv2.COLOR_BGR2GRAY)
    ring = np.ones(outer_gray.shape, dtype=bool)
    ring[
        y0 - outer_y0 : y1 - outer_y0,
        x0 - outer_x0 : x1 - outer_x0,
    ] = False
    ring_pixels = outer_gray[ring]
    if not ring_pixels.size:
        return 0.0
    background_delta = abs(
        float(np.percentile(inner_gray, 82))
        - float(np.percentile(ring_pixels, 82))
    ) / 24.0
    inner_noise = float(
        np.median(np.abs(cv2.Laplacian(inner_gray, cv2.CV_32F)))
    )
    outer_laplacian = np.abs(cv2.Laplacian(outer_gray, cv2.CV_32F))
    outer_noise = float(np.median(outer_laplacian[ring]))
    noise_ratio = abs(np.log((inner_noise + 1.0) / (outer_noise + 1.0))) / 2.5
    boundary = cv2.Canny(outer_gray, 55, 150)
    local_x0, local_y0 = x0 - outer_x0, y0 - outer_y0
    local_x1, local_y1 = x1 - outer_x0, y1 - outer_y0
    band = max(2, min(margin, 8))
    boundary_samples = (
        boundary[
            max(0, local_y0 - band) : min(boundary.shape[0], local_y0 + band),
            local_x0:local_x1,
        ],
        boundary[
            max(0, local_y1 - band) : min(boundary.shape[0], local_y1 + band),
            local_x0:local_x1,
        ],
        boundary[
            local_y0:local_y1,
            max(0, local_x0 - band) : min(boundary.shape[1], local_x0 + band),
        ],
        boundary[
            local_y0:local_y1,
            max(0, local_x1 - band) : min(boundary.shape[1], local_x1 + band),
        ],
    )
    boundary_density = float(
        np.mean([np.mean(sample > 0) for sample in boundary_samples if sample.size])
    )
    return round(min(100.0, 100.0 * (0.56 * min(1.0, background_delta) + 0.24 * min(1.0, noise_ratio) + 0.20 * min(1.0, boundary_density * 8))), 1)


def _placement_anomalies(crop: np.ndarray) -> tuple[float, float]:
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    points = cv2.findNonZero(mask)
    if points is None:
        return 0.0, 0.0
    x, y, ink_width, ink_height = cv2.boundingRect(points)
    crop_height, crop_width = mask.shape
    centre_x = (x + ink_width / 2.0) / max(crop_width, 1)
    centre_y = (y + ink_height / 2.0) / max(crop_height, 1)
    centre_distance = float(np.hypot(centre_x - 0.5, centre_y - 0.5))
    position = 100.0 * min(1.0, max(0.0, centre_distance - 0.28) / 0.24)
    width_ratio = ink_width / max(crop_width, 1)
    height_ratio = ink_height / max(crop_height, 1)
    too_small = max(
        max(0.0, 0.18 - width_ratio) / 0.18,
        max(0.0, 0.10 - height_ratio) / 0.10,
    )
    too_large = max(
        max(0.0, width_ratio - 0.96) / 0.04,
        max(0.0, height_ratio - 0.90) / 0.10,
    )
    scale = 100.0 * min(1.0, max(too_small, too_large))
    return round(position, 1), round(scale, 1)


def _region_explanation(
    kind: BiometricKind,
    score: float,
    measurements: dict[str, float],
    compositing: float,
) -> str:
    ordered = sorted(measurements.items(), key=lambda item: (-item[1], item[0]))
    strongest = ", ".join(name.replace("_", " ") for name, _ in ordered[:2])
    weakest = ordered[-1][0].replace("_", " ")
    text = f"{kind.title()} appearance score {score:.1f}; strongest agreement in {strongest}, weakest in {weakest}."
    if kind == "signature":
        text += f" Independent compositing indicator {compositing:.1f}."
    return text


def _aggregate_reasons(
    kind: BiometricKind,
    measurements: Sequence[dict[str, float]],
    score: float,
    compositing: float | None,
) -> list[str]:
    averages = {
        name: float(np.mean([item[name] for item in measurements]))
        for name in measurements[0]
    }
    ordered = sorted(averages.items(), key=lambda item: (-item[1], item[0]))
    reasons = [
        f"Strongest ensemble feature: {ordered[0][0].replace('_', ' ')} ({ordered[0][1] * 100:.1f}).",
        f"Weakest ensemble feature: {ordered[-1][0].replace('_', ' ')} ({ordered[-1][1] * 100:.1f}).",
        f"Aggregate {kind} appearance consistency: {score:.1f}.",
    ]
    if compositing is not None:
        reasons.append(f"Independent paste/background compositing indicator: {compositing:.1f}.")
    return reasons


def _identity_limitation(kind: BiometricKind) -> str:
    return f"{kind.title()} appearance similarity is not legal identity or definitive authorship proof."
