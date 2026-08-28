"""Conservative ORB/RANSAC alignment with a dimension-safe fallback."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(slots=True)
class AlignmentResult:
    aligned_reference: np.ndarray
    matrix: np.ndarray
    method: str
    quality: float
    match_count: int
    inlier_ratio: float
    reprojection_error: float | None


def align_reference(reference: np.ndarray, candidate: np.ndarray) -> AlignmentResult:
    candidate_height, candidate_width = candidate.shape[:2]
    reference_height, reference_width = reference.shape[:2]
    if reference.shape == candidate.shape and np.array_equal(reference, candidate):
        identity = np.eye(3, dtype=np.float64)
        return AlignmentResult(
            aligned_reference=reference.copy(),
            matrix=identity,
            method="exact_pixel_identity",
            quality=1.0,
            match_count=0,
            inlier_ratio=1.0,
            reprojection_error=0.0,
        )
    gray_reference = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)
    gray_candidate = cv2.cvtColor(candidate, cv2.COLOR_BGR2GRAY)
    orb = cv2.ORB_create(
        nfeatures=3500,
        scaleFactor=1.2,
        nlevels=8,
        edgeThreshold=20,
        fastThreshold=10,
    )
    keypoints_reference, descriptors_reference = orb.detectAndCompute(gray_reference, None)
    keypoints_candidate, descriptors_candidate = orb.detectAndCompute(gray_candidate, None)

    if descriptors_reference is not None and descriptors_candidate is not None:
        matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
        pairs = matcher.knnMatch(descriptors_reference, descriptors_candidate, k=2)
        good = [
            pair[0]
            for pair in pairs
            if len(pair) == 2 and pair[0].distance < 0.76 * pair[1].distance
        ]
        if len(good) >= 10:
            source = np.float32(
                [keypoints_reference[match.queryIdx].pt for match in good]
            ).reshape(-1, 1, 2)
            target = np.float32(
                [keypoints_candidate[match.trainIdx].pt for match in good]
            ).reshape(-1, 1, 2)
            matrix, inlier_mask = cv2.findHomography(
                source, target, cv2.RANSAC, ransacReprojThreshold=4.0
            )
            if matrix is not None and inlier_mask is not None:
                inliers = inlier_mask.ravel().astype(bool)
                inlier_count = int(inliers.sum())
                inlier_ratio = inlier_count / len(good)
                if inlier_count >= 8 and _reasonable_homography(
                    matrix,
                    reference_width,
                    reference_height,
                    candidate_width,
                    candidate_height,
                ):
                    projected = cv2.perspectiveTransform(source[inliers], matrix)
                    errors = np.linalg.norm(
                        projected.reshape(-1, 2) - target[inliers].reshape(-1, 2), axis=1
                    )
                    reprojection_error = float(np.median(errors)) if errors.size else 4.0
                    match_strength = min(1.0, len(good) / 100.0)
                    error_factor = max(0.0, 1.0 - reprojection_error / 8.0)
                    quality = float(
                        np.clip(
                            0.30 + 0.40 * inlier_ratio + 0.15 * match_strength + 0.15 * error_factor,
                            0.0,
                            1.0,
                        )
                    )
                    method = "orb_homography"
                    if (
                        reference_width == candidate_width
                        and reference_height == candidate_height
                        and _corner_displacement(
                            matrix, reference_width, reference_height
                        ) < 0.75
                    ):
                        matrix = np.eye(3, dtype=np.float64)
                        method = "orb_identity_snap"
                    aligned = cv2.warpPerspective(
                        reference,
                        matrix,
                        (candidate_width, candidate_height),
                        flags=cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_CONSTANT,
                        borderValue=(255, 255, 255),
                    )
                    return AlignmentResult(
                        aligned_reference=aligned,
                        matrix=matrix,
                        method=method,
                        quality=quality,
                        match_count=len(good),
                        inlier_ratio=inlier_ratio,
                        reprojection_error=reprojection_error,
                    )

    scale_x = candidate_width / reference_width
    scale_y = candidate_height / reference_height
    matrix = np.array([[scale_x, 0.0, 0.0], [0.0, scale_y, 0.0], [0.0, 0.0, 1.0]])
    aligned = cv2.resize(reference, (candidate_width, candidate_height), interpolation=cv2.INTER_AREA)
    aspect_reference = reference_width / reference_height
    aspect_candidate = candidate_width / candidate_height
    aspect_error = abs(aspect_reference - aspect_candidate) / max(aspect_reference, aspect_candidate)
    quality = float(np.clip(0.65 - 2.5 * aspect_error, 0.25, 0.65))
    return AlignmentResult(
        aligned_reference=aligned,
        matrix=matrix,
        method="page_dimension_fallback",
        quality=quality,
        match_count=0,
        inlier_ratio=0.0,
        reprojection_error=None,
    )


def _reasonable_homography(
    matrix: np.ndarray,
    reference_width: int,
    reference_height: int,
    candidate_width: int,
    candidate_height: int,
) -> bool:
    if not np.isfinite(matrix).all() or abs(matrix[2, 2]) < 1e-8:
        return False
    corners = np.float32(
        [[0, 0], [reference_width, 0], [reference_width, reference_height], [0, reference_height]]
    ).reshape(-1, 1, 2)
    projected = cv2.perspectiveTransform(corners, matrix).reshape(-1, 2)
    if not np.isfinite(projected).all():
        return False
    margin_x = candidate_width * 0.15
    margin_y = candidate_height * 0.15
    if (
        projected[:, 0].min() < -margin_x
        or projected[:, 0].max() > candidate_width + margin_x
        or projected[:, 1].min() < -margin_y
        or projected[:, 1].max() > candidate_height + margin_y
    ):
        return False
    mapped_area = abs(cv2.contourArea(projected.astype(np.float32)))
    target_area = float(candidate_width * candidate_height)
    area_ratio = mapped_area / max(target_area, 1.0)
    return 0.65 <= area_ratio <= 1.35


def _corner_displacement(matrix: np.ndarray, width: int, height: int) -> float:
    corners = np.float32([[0, 0], [width, 0], [width, height], [0, height]]).reshape(-1, 1, 2)
    projected = cv2.perspectiveTransform(corners, matrix).reshape(-1, 2)
    return float(np.max(np.linalg.norm(projected - corners.reshape(-1, 2), axis=1)))
