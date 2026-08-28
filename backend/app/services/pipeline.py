"""Real in-process analysis lifecycle built from deterministic forensic stages."""

from __future__ import annotations

import logging
import json
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np

from backend.app.core.config import Settings
from backend.app.core.storage import JobStore
from backend.app.forensics.alignment import AlignmentResult, align_reference
from backend.app.forensics.differences import DifferenceRegion, DifferenceResult, localize_differences
from backend.app.forensics.scoring import finding_scores, overall_score, risk_label, severity
from backend.app.forensics.text import TextComparison, compare_text
from backend.app.models.contracts import (
    AnalysisJob,
    AssetLinks,
    BoundingBox,
    CreateAnalysisResponse,
    DocumentDescriptor,
    DocumentResult,
    ErrorDetail,
    Finding,
    JobState,
    PageResult,
    StageId,
    TextExtractionSummary,
)
from backend.app.services.documents import (
    RenderedDocument,
    TextExtraction,
    ValidatedUpload,
    extract_text,
    render_document,
    save_upload,
    write_png,
)


LOGGER = logging.getLogger(__name__)
API_PREFIX = "/api/v1"


class AnalysisManager:
    def __init__(self, settings: Settings, store: JobStore) -> None:
        self.settings = settings
        self.store = store
        self.executor = ThreadPoolExecutor(
            max_workers=settings.worker_count, thread_name_prefix="docuverify-analysis"
        )
        self._futures: dict[str, Future[None]] = {}

    def submit(
        self, reference: ValidatedUpload, candidate: ValidatedUpload
    ) -> CreateAnalysisResponse:
        job_id = str(uuid.uuid4())
        self.store.cleanup_expired(self.settings.retention_hours)
        job_dir = self.store.job_directory(job_id)
        save_upload(reference, job_dir, "reference")
        save_upload(candidate, job_dir, "candidate")
        self.store.create_job(job_id)
        future = self.executor.submit(self._run_guarded, job_id, reference, candidate)
        self._futures[job_id] = future
        return CreateAnalysisResponse(
            job_id=job_id,
            state=JobState.QUEUED,
            status_url=f"{API_PREFIX}/analyses/{job_id}",
            events_url=f"{API_PREFIX}/analyses/{job_id}/events",
        )

    def _run_guarded(
        self, job_id: str, reference: ValidatedUpload, candidate: ValidatedUpload
    ) -> None:
        try:
            self._run(job_id, reference, candidate)
        except Exception:
            LOGGER.exception("Analysis job %s failed", job_id)
            current = self.store.get_job(job_id)
            progress = current.progress if current else 0
            error = ErrorDetail(
                code="analysis_failed",
                message="The analysis could not be completed. The uploaded files remain private; please retry.",
            )
            self.store.update_job(
                job_id,
                state=JobState.FAILED,
                progress=progress,
                stage=StageId.FAILED,
                message=error.message,
                error=error,
            )
            self.store.append_event(
                job_id,
                event_type="error",
                stage=StageId.FAILED,
                message=error.message,
                progress=progress,
            )

    def _stage(
        self,
        job_id: str,
        stage: StageId,
        message: str,
        progress: int,
        finding_count: int = 0,
        candidate_page_url: str | None = None,
    ) -> None:
        self.store.update_job(
            job_id,
            state=JobState.RUNNING,
            progress=progress,
            stage=stage,
            message=message,
        )
        self.store.append_event(
            job_id,
            event_type="progress",
            stage=stage,
            message=message,
            progress=progress,
            finding_count=finding_count,
            candidate_page_url=candidate_page_url,
        )

    def _run(
        self, job_id: str, reference: ValidatedUpload, candidate: ValidatedUpload
    ) -> None:
        started = time.perf_counter()
        job_dir = self.store.job_directory(job_id)
        assets_dir = job_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)

        self._stage(
            job_id,
            StageId.VALIDATING_UPLOADS,
            "Validating file contents, hashes, and single-page limits",
            5,
        )
        self._verify_saved_inputs(job_dir, reference, candidate)

        self._stage(
            job_id,
            StageId.RENDERING_DOCUMENTS,
            "Rendering both document pages into safe local images",
            16,
        )
        rendered_reference = render_document(reference, self.settings.max_render_dimension)
        rendered_candidate = render_document(candidate, self.settings.max_render_dimension)

        self._stage(
            job_id,
            StageId.NORMALIZING_PAGES,
            "Normalizing page orientation, dimensions, and color space",
            27,
        )
        reference_image = _normalize_image(rendered_reference.image)
        candidate_image = _normalize_image(rendered_candidate.image)
        reference_page_path = assets_dir / "reference-page.png"
        candidate_page_path = assets_dir / "candidate-page.png"
        write_png(reference_page_path, reference_image)
        write_png(candidate_page_path, candidate_image)
        self.store.register_asset(job_id, "reference-page", reference_page_path)
        self.store.register_asset(job_id, "candidate-page", candidate_page_path)

        self._stage(
            job_id,
            StageId.ALIGNING_REFERENCE,
            "Aligning the trusted reference to the questioned page",
            39,
            candidate_page_url=_asset_url(job_id, "candidate-page"),
        )
        alignment = align_reference(reference_image, candidate_image)

        self._stage(
            job_id,
            StageId.EXTRACTING_TEXT,
            "Extracting available embedded text and labels",
            50,
        )
        reference_text = extract_text(reference)
        candidate_text = extract_text(candidate)

        self._stage(
            job_id,
            StageId.COMPARING_STRUCTURE,
            "Comparing document structure and extracted text",
            61,
        )
        text_comparison = compare_text(reference_text, candidate_text)

        self._stage(
            job_id,
            StageId.LOCALIZING_DIFFERENCES,
            "Localizing visual and textual differences on the questioned page",
            73,
        )
        differences = localize_differences(
            alignment.aligned_reference, candidate_image, text_comparison.changes
        )

        self._stage(
            job_id,
            StageId.SCORING_EVIDENCE,
            "Scoring localized forensic evidence",
            84,
            len(differences.regions),
        )
        findings = self._build_findings(
            job_id,
            assets_dir,
            candidate_image,
            alignment,
            differences,
        )

        self._stage(
            job_id,
            StageId.PREPARING_RESULT,
            "Preparing the explainable result and browser-safe evidence",
            94,
            len(findings),
        )
        result = self._build_result(
            job_id=job_id,
            reference=reference,
            candidate=candidate,
            rendered_reference=rendered_reference,
            rendered_candidate=rendered_candidate,
            reference_image=reference_image,
            candidate_image=candidate_image,
            alignment=alignment,
            differences=differences,
            findings=findings,
            reference_text=reference_text,
            candidate_text=candidate_text,
            text_comparison=text_comparison,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        metadata = {
            "schema_version": "1.0",
            "job_id": job_id,
            "reference_transform": rendered_reference.transform.model_dump(mode="json"),
            "candidate_transform": rendered_candidate.transform.model_dump(mode="json"),
            "reference_to_candidate_homography": alignment.matrix.tolist(),
            "alignment_method": alignment.method,
            "alignment_quality": alignment.quality,
        }
        (job_dir / "analysis-metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        self.store.update_job(
            job_id,
            state=JobState.COMPLETED,
            progress=100,
            stage=StageId.COMPLETE,
            message="Analysis complete",
            result=result,
        )
        self.store.append_event(
            job_id,
            event_type="complete",
            stage=StageId.COMPLETE,
            message="Analysis complete",
            progress=100,
            finding_count=len(findings),
        )

    @staticmethod
    def _verify_saved_inputs(
        job_dir: Path, reference: ValidatedUpload, candidate: ValidatedUpload
    ) -> None:
        for role, upload in (("reference", reference), ("candidate", candidate)):
            saved = job_dir / "inputs" / f"{role}{upload.extension}"
            if not saved.is_file() or saved.read_bytes() != upload.data:
                raise RuntimeError(f"Saved {role} input failed integrity verification")

    def _build_findings(
        self,
        job_id: str,
        assets_dir: Path,
        candidate_image: np.ndarray,
        alignment: AlignmentResult,
        differences: DifferenceResult,
    ) -> list[Finding]:
        height, width = candidate_image.shape[:2]
        page_area = width * height
        findings: list[Finding] = []
        for index, region in enumerate(differences.regions[:5], start=1):
            finding_id = f"finding-{index:03d}"
            candidate_crop = candidate_image[region.y0 : region.y1, region.x0 : region.x1].copy()
            reference_crop = alignment.aligned_reference[
                region.y0 : region.y1, region.x0 : region.x1
            ].copy()
            overlay = _difference_overlay(candidate_crop, differences.mask, region)
            candidate_asset_id = f"{finding_id}-candidate"
            reference_asset_id = f"{finding_id}-reference"
            overlay_asset_id = f"{finding_id}-overlay"
            candidate_path = assets_dir / f"{candidate_asset_id}.png"
            reference_path = assets_dir / f"{reference_asset_id}.png"
            overlay_path = assets_dir / f"{overlay_asset_id}.png"
            write_png(candidate_path, candidate_crop)
            write_png(reference_path, reference_crop)
            write_png(overlay_path, overlay)
            self.store.register_asset(job_id, candidate_asset_id, candidate_path)
            self.store.register_asset(job_id, reference_asset_id, reference_path)
            self.store.register_asset(job_id, overlay_asset_id, overlay_path)

            region_area = max(1, region.width * region.height)
            has_text_change = bool(region.text_changes)
            risk, confidence = finding_scores(
                changed_pixels=region.changed_pixels,
                region_area=region_area,
                page_area=page_area,
                mean_delta=region.mean_delta,
                alignment_quality=alignment.quality,
                has_text_change=has_text_change,
            )
            before = " | ".join(change.before for change in region.text_changes)[:160]
            after = " | ".join(change.after for change in region.text_changes)[:160]
            if has_text_change:
                title = "Text content differs from the trusted reference"
                explanation = (
                    f"The trusted reference contains “{before}”, while the questioned document "
                    f"contains “{after}”. The pixel comparison localizes the change to this region."
                )
                category = "text_content_change"
            else:
                title = "Visual content differs from the trusted reference"
                explanation = (
                    "Aligned pixels and document edges differ in this localized region. "
                    "The marker shows where the questioned page diverges from the trusted reference."
                )
                category = "visual_content_change"
            bounding_box = _normalized_box(region, width, height)
            findings.append(
                Finding(
                    finding_id=finding_id,
                    page_number=1,
                    category=category,
                    title=title,
                    explanation=explanation,
                    bounding_box=bounding_box,
                    risk_score=risk,
                    confidence_score=confidence,
                    severity=severity(risk),
                    evidence_source=sorted(region.evidence_sources),
                    assets=AssetLinks(
                        candidate_crop_url=_asset_url(job_id, candidate_asset_id),
                        reference_crop_url=_asset_url(job_id, reference_asset_id),
                        difference_overlay_url=_asset_url(job_id, overlay_asset_id),
                    ),
                    supporting_measurements={
                        "changed_pixel_count": region.changed_pixels,
                        "region_pixel_area": region_area,
                        "changed_pixel_density": round(region.changed_pixels / region_area, 5),
                        "mean_pixel_delta": round(region.mean_delta, 2),
                        "max_pixel_delta": region.max_delta,
                        "edge_changed_pixels": region.edge_changed_pixels,
                        "alignment_method": alignment.method,
                        "alignment_inlier_ratio": round(alignment.inlier_ratio, 4),
                        "text_before": before or None,
                        "text_after": after or None,
                    },
                )
            )
        return findings

    @staticmethod
    def _build_result(
        *,
        job_id: str,
        reference: ValidatedUpload,
        candidate: ValidatedUpload,
        rendered_reference: RenderedDocument,
        rendered_candidate: RenderedDocument,
        reference_image: np.ndarray,
        candidate_image: np.ndarray,
        alignment: AlignmentResult,
        differences: DifferenceResult,
        findings: list[Finding],
        reference_text: TextExtraction,
        candidate_text: TextExtraction,
        text_comparison: TextComparison,
        duration_ms: int,
    ) -> DocumentResult:
        reference_height, reference_width = reference_image.shape[:2]
        candidate_height, candidate_width = candidate_image.shape[:2]
        risks = [finding.risk_score for finding in findings]
        total_risk = overall_score(risks, differences.global_changed_ratio)
        text_available = bool(reference_text.text and candidate_text.text)
        confidence = min(99.0, 65.0 + 27.0 * alignment.quality + (7.0 if text_available else 0.0))
        coverage = 100.0 if text_available else 88.0
        reference_descriptor = DocumentDescriptor(
            filename=reference.filename,
            content_type=reference.content_type,
            sha256=reference.sha256,
            page_count=1,
            width=reference_width,
            height=reference_height,
            preview_url=_asset_url(job_id, "reference-page"),
            transform=rendered_reference.transform,
        )
        candidate_descriptor = DocumentDescriptor(
            filename=candidate.filename,
            content_type=candidate.content_type,
            sha256=candidate.sha256,
            page_count=1,
            width=candidate_width,
            height=candidate_height,
            preview_url=_asset_url(job_id, "candidate-page"),
            transform=rendered_candidate.transform,
        )
        return DocumentResult(
            job_id=job_id,
            reference=reference_descriptor,
            candidate=candidate_descriptor,
            pages=[
                PageResult(
                    page_number=1,
                    width=candidate_width,
                    height=candidate_height,
                    reference_image_url=reference_descriptor.preview_url,
                    candidate_image_url=candidate_descriptor.preview_url,
                    findings=findings,
                )
            ],
            overall_tampering_risk=total_risk,
            risk_label=risk_label(total_risk),
            assessment_confidence=round(confidence, 1),
            analysis_coverage=coverage,
            alignment_quality=round(alignment.quality * 100.0, 1),
            finding_count=len(findings),
            processing_duration_ms=max(0, duration_ms),
            text_extraction=TextExtractionSummary(
                reference_source=reference_text.source,
                candidate_source=candidate_text.source,
                reference_characters=len(reference_text.text),
                candidate_characters=len(candidate_text.text),
                similarity=(
                    round(text_comparison.similarity, 6)
                    if text_comparison.similarity is not None
                    else None
                ),
            ),
            generated_at=datetime.now(UTC),
        )

    def wait(self, job_id: str, timeout: float = 30.0) -> AnalysisJob:
        future = self._futures.get(job_id)
        if future is not None:
            future.result(timeout=timeout)
        job = self.store.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def shutdown(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=False)


def _normalize_image(image: np.ndarray) -> np.ndarray:
    if image.dtype != np.uint8:
        image = np.clip(image, 0, 255).astype(np.uint8)
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.shape[2] == 4:
        image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    return np.ascontiguousarray(image)


def _normalized_box(region: DifferenceRegion, width: int, height: int) -> BoundingBox:
    x = round(region.x0 / width, 6)
    y = round(region.y0 / height, 6)
    box_width = round((region.x1 - region.x0) / width, 6)
    box_height = round((region.y1 - region.y0) / height, 6)
    box_width = min(box_width, round(1.0 - x, 6))
    box_height = min(box_height, round(1.0 - y, 6))
    return BoundingBox(x=x, y=y, width=max(box_width, 0.000001), height=max(box_height, 0.000001))


def _difference_overlay(
    candidate_crop: np.ndarray, mask: np.ndarray, region: DifferenceRegion
) -> np.ndarray:
    overlay = candidate_crop.copy()
    local_mask = mask[region.y0 : region.y1, region.x0 : region.x1] > 0
    if np.any(local_mask):
        red = np.zeros_like(overlay)
        red[:, :] = (30, 40, 235)
        blended = cv2.addWeighted(overlay, 0.48, red, 0.52, 0)
        overlay[local_mask] = blended[local_mask]
    cv2.rectangle(
        overlay,
        (1, 1),
        (max(1, overlay.shape[1] - 2), max(1, overlay.shape[0] - 2)),
        (20, 85, 245),
        2,
    )
    return overlay


def _asset_url(job_id: str, asset_id: str) -> str:
    return f"{API_PREFIX}/analyses/{job_id}/assets/{asset_id}"
