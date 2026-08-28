"""Deterministic, bounded multi-page document-analysis lifecycle."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from threading import Lock
from typing import Any

import cv2
import numpy as np

from backend.app.core.config import Settings
from backend.app.core.storage import JobStore
from backend.app.forensics.alignment import AlignmentResult, align_reference
from backend.app.forensics.differences import DifferenceRegion, DifferenceResult, localize_differences
from backend.app.forensics.scoring import finding_scores, overall_score, risk_label, severity
from backend.app.forensics.text import compare_text
from backend.app.models.contracts import (
    AnalysisJob,
    AssetLinks,
    BoundingBox,
    ComparisonMode,
    CreateAnalysisResponse,
    DocumentAggregate,
    DocumentDescriptor,
    DocumentResult,
    ErrorDetail,
    Finding,
    JobState,
    PageAnomalyType,
    PageCorrespondence,
    PageOCRSummary,
    PageOrderAnomaly,
    PageResult,
    PageStatus,
    RegionRole,
    RegionSuggestion,
    StageId,
    TextExtractionSummary,
)
from backend.app.services import documents
from backend.app.services.documents import RenderedDocument, TextExtraction, ValidatedUpload
from backend.app.services.metadata import MetadataChange, compare_document_metadata


LOGGER = logging.getLogger(__name__)
API_PREFIX = "/api/v1"
PAGE_CORRESPONDENCE_MIN_SCORE = 0.82
PAGE_CORRESPONDENCE_MIN_HEADING = 0.72


@dataclass(slots=True)
class _PreparedPage:
    page_number: int
    image_path: Path
    asset_id: str
    transform: Any
    width: int
    height: int
    original_width: int
    original_height: int
    text: TextExtraction
    thumbnail: np.ndarray
    layout: np.ndarray
    heading: str


@dataclass(frozen=True, slots=True)
class _PageMatch:
    reference_index: int | None
    candidate_index: int | None
    status: PageStatus
    score: float
    heading_similarity: float | None
    perceptual_similarity: float | None
    dimension_similarity: float | None


class AnalysisManager:
    def __init__(self, settings: Settings, store: JobStore) -> None:
        self.settings = settings
        self.store = store
        self.executor = ThreadPoolExecutor(
            max_workers=settings.worker_count, thread_name_prefix="docuverify-analysis"
        )
        self._futures: dict[str, Future[None]] = {}
        self._futures_lock = Lock()

    def submit(
        self,
        reference: ValidatedUpload,
        candidate: ValidatedUpload,
        comparison_mode: ComparisonMode | str = ComparisonMode.EXACT,
    ) -> CreateAnalysisResponse:
        mode = ComparisonMode(comparison_mode)
        job_id = str(uuid.uuid4())
        self.store.cleanup_expired(self.settings.retention_hours)
        job_dir = self.store.job_directory(job_id)
        documents.save_upload(reference, job_dir, "reference")
        documents.save_upload(candidate, job_dir, "candidate")
        total_pages = max(int(reference.page_count), int(candidate.page_count))
        self.store.create_job(job_id, total_pages=total_pages)
        future = self.executor.submit(
            self._run_guarded, job_id, reference, candidate, mode
        )
        with self._futures_lock:
            self._futures[job_id] = future
        future.add_done_callback(
            lambda completed, submitted_job_id=job_id: self._forget_future(
                submitted_job_id, completed
            )
        )
        return CreateAnalysisResponse(
            job_id=job_id,
            state=JobState.QUEUED,
            status_url=f"{API_PREFIX}/analyses/{job_id}",
            events_url=f"{API_PREFIX}/analyses/{job_id}/events",
        )

    def _forget_future(self, job_id: str, completed: Future[None]) -> None:
        with self._futures_lock:
            if self._futures.get(job_id) is completed:
                self._futures.pop(job_id, None)

    def _run_guarded(
        self,
        job_id: str,
        reference: ValidatedUpload,
        candidate: ValidatedUpload,
        comparison_mode: ComparisonMode,
    ) -> None:
        total_pages = max(int(reference.page_count), int(candidate.page_count))
        try:
            self._run(job_id, reference, candidate, comparison_mode)
        except Exception:
            LOGGER.exception("Analysis job %s failed", job_id)
            current = self.store.get_job(job_id)
            progress = current.progress if current else 0
            error = ErrorDetail(
                code="analysis_failed",
                message=(
                    "The analysis could not be completed. The uploaded files remain "
                    "private; please retry."
                ),
            )
            self.store.update_job(
                job_id,
                state=JobState.FAILED,
                progress=progress,
                stage=StageId.FAILED,
                message=error.message,
                error=error,
                current_page=min(current.current_page if current else 1, total_pages),
                total_pages=total_pages,
                candidate_page_url=current.candidate_page_url if current else None,
            )
            self.store.append_event(
                job_id,
                event_type="error",
                stage=StageId.FAILED,
                page_stage=StageId.FAILED,
                message=error.message,
                progress=progress,
                page_number=min(current.current_page if current else 1, total_pages),
                total_pages=total_pages,
            )

    def _stage(
        self,
        job_id: str,
        stage: StageId,
        message: str,
        progress: int,
        *,
        page_number: int = 1,
        total_pages: int = 1,
        finding_count: int = 0,
        candidate_page_url: str | None = None,
        ocr_provider: str | None = None,
        localized_region: BoundingBox | None = None,
    ) -> None:
        current = self.store.get_job(job_id)
        if current is not None:
            progress = max(current.progress, progress)
        self.store.update_job(
            job_id,
            state=JobState.RUNNING,
            progress=progress,
            stage=stage,
            message=message,
            current_page=page_number,
            total_pages=total_pages,
            candidate_page_url=candidate_page_url,
        )
        self.store.append_event(
            job_id,
            event_type="progress",
            stage=stage,
            page_stage=stage,
            message=message,
            progress=progress,
            page_number=page_number,
            total_pages=total_pages,
            finding_count=finding_count,
            candidate_page_url=candidate_page_url,
            ocr_provider=ocr_provider,
            localized_region=localized_region,
        )

    def _run(
        self,
        job_id: str,
        reference: ValidatedUpload,
        candidate: ValidatedUpload,
        comparison_mode: ComparisonMode,
    ) -> None:
        started = time.perf_counter()
        job_dir = self.store.job_directory(job_id)
        assets_dir = job_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        reference_count = int(reference.page_count)
        candidate_count = int(candidate.page_count)
        total_pages = max(reference_count, candidate_count)

        self._stage(
            job_id,
            StageId.VALIDATING_UPLOADS,
            f"Validating uploads and the {total_pages}-page analysis plan",
            5,
            total_pages=total_pages,
        )
        self._verify_saved_inputs(job_dir, reference, candidate)

        reference_pages: list[_PreparedPage] = []
        candidate_pages: list[_PreparedPage] = []
        preparation_span = 24 / total_pages
        for page_index in range(total_pages):
            page_number = page_index + 1
            self._stage(
                job_id,
                StageId.RENDERING_DOCUMENTS,
                f"Rendering page {page_number} of {total_pages}",
                round(6 + preparation_span * page_index),
                page_number=page_number,
                total_pages=total_pages,
            )
            rendered_reference = (
                _render_document_page(
                    reference, page_index, self.settings.max_render_dimension
                )
                if page_index < reference_count
                else None
            )
            rendered_candidate = (
                _render_document_page(
                    candidate, page_index, self.settings.max_render_dimension
                )
                if page_index < candidate_count
                else None
            )
            self._stage(
                job_id,
                StageId.NORMALIZING_PAGES,
                f"Normalizing page {page_number} of {total_pages}",
                round(6 + preparation_span * (page_index + 1 / 3)),
                page_number=page_number,
                total_pages=total_pages,
            )
            reference_page = (
                self._prepare_page(
                    job_id,
                    assets_dir,
                    "reference",
                    page_index,
                    rendered_reference,
                )
                if rendered_reference is not None
                else None
            )
            candidate_page = (
                self._prepare_page(
                    job_id,
                    assets_dir,
                    "candidate",
                    page_index,
                    rendered_candidate,
                )
                if rendered_candidate is not None
                else None
            )
            candidate_url = (
                _asset_url(
                    job_id,
                    "candidate-page"
                    if total_pages == 1
                    else candidate_page.asset_id,
                )
                if candidate_page is not None
                else None
            )
            self._stage(
                job_id,
                StageId.EXTRACTING_TEXT,
                f"Extracting text from page {page_number} of {total_pages}",
                round(6 + preparation_span * (page_index + 2 / 3)),
                page_number=page_number,
                total_pages=total_pages,
                candidate_page_url=candidate_url,
            )
            if reference_page is not None and rendered_reference is not None:
                self._set_page_text(
                    reference_page,
                    _extract_page_text(
                        reference,
                        rendered_reference,
                        page_index,
                        self.settings.ocr_provider_preference,
                    ),
                )
                reference_pages.append(reference_page)
            if candidate_page is not None and rendered_candidate is not None:
                self._set_page_text(
                    candidate_page,
                    _extract_page_text(
                        candidate,
                        rendered_candidate,
                        page_index,
                        self.settings.ocr_provider_preference,
                    ),
                )
                candidate_pages.append(candidate_page)
            del rendered_reference, rendered_candidate

        matches = _estimate_page_correspondence(reference_pages, candidate_pages)
        anomalies = _page_anomalies(matches, reference_pages, candidate_pages)
        metadata_changes = (
            compare_document_metadata(reference, candidate)
            if comparison_mode is ComparisonMode.EXACT
            else ()
        )
        metadata_attached = False
        pages: list[PageResult] = []
        suggestions: list[RegionSuggestion] = []
        metadata_pages: list[dict[str, Any]] = []
        similarities: list[float] = []
        cumulative_findings = 0

        for display_index, match in enumerate(matches, start=1):
            page_span = 64 / max(len(matches), 1)
            compare_start = 30 + page_span * (display_index - 1)
            event_page_number = (
                candidate_pages[match.candidate_index].page_number
                if match.candidate_index is not None
                else reference_pages[match.reference_index].page_number
            )
            candidate_url = (
                _asset_url(
                    job_id,
                    "candidate-page"
                    if total_pages == 1
                    else candidate_pages[match.candidate_index].asset_id,
                )
                if match.candidate_index is not None
                else None
            )
            if match.reference_index is None or match.candidate_index is None:
                page = self._build_unmatched_page(
                    job_id,
                    assets_dir,
                    display_index,
                    match,
                    reference_pages,
                    candidate_pages,
                )
                pages.append(page)
                cumulative_findings += page.finding_count
                self._stage(
                    job_id,
                    StageId.COMPARING_STRUCTURE,
                    _unmatched_message(match),
                    round(compare_start + page_span * 0.84),
                    page_number=event_page_number,
                    total_pages=total_pages,
                    finding_count=cumulative_findings,
                    candidate_page_url=candidate_url,
                )
                continue

            reference_page = reference_pages[match.reference_index]
            candidate_page = candidate_pages[match.candidate_index]
            reference_image = _read_page_image(reference_page.image_path)
            candidate_image = _read_page_image(candidate_page.image_path)
            self._stage(
                job_id,
                StageId.ALIGNING_REFERENCE,
                (
                    f"Aligning page {event_page_number} with reference page "
                    f"{reference_page.page_number}"
                ),
                round(compare_start + page_span * 0.14),
                page_number=event_page_number,
                total_pages=total_pages,
                finding_count=cumulative_findings,
                candidate_page_url=candidate_url,
            )
            alignment = align_reference(reference_image, candidate_image)

            provider = _provider_name(candidate_page.text)
            text_comparison = compare_text(
                reference_page.text,
                candidate_page.text,
                comparison_mode=comparison_mode.value,
            )
            if text_comparison.similarity is not None:
                similarities.append(text_comparison.similarity)
            self._stage(
                job_id,
                StageId.COMPARING_STRUCTURE,
                (
                    "Identifying fixed and variable fields"
                    if comparison_mode is ComparisonMode.TEMPLATE
                    else f"Comparing document structure on page {event_page_number}"
                ),
                round(compare_start + page_span * 0.48),
                page_number=event_page_number,
                total_pages=total_pages,
                finding_count=cumulative_findings,
                candidate_page_url=candidate_url,
                ocr_provider=provider,
            )

            differences = localize_differences(
                alignment.aligned_reference,
                candidate_image,
                text_comparison.changes,
                reference_to_candidate_matrix=alignment.matrix,
                reference_size=(reference_image.shape[1], reference_image.shape[0]),
            )
            self._stage(
                job_id,
                StageId.LOCALIZING_DIFFERENCES,
                f"Localizing evidence on page {event_page_number}",
                round(compare_start + page_span * 0.65),
                page_number=event_page_number,
                total_pages=total_pages,
                finding_count=cumulative_findings,
                candidate_page_url=candidate_url,
                ocr_provider=provider,
            )
            findings, page_suggestions = self._build_findings(
                job_id,
                assets_dir,
                display_index,
                candidate_image,
                alignment,
                differences,
                reference_page.text,
                candidate_page.text,
                comparison_mode,
                single_page=(total_pages == 1),
            )
            findings.extend(
                self._build_match_anomaly_findings(
                    job_id,
                    assets_dir,
                    display_index,
                    match,
                    reference_page,
                    candidate_page,
                )
            )
            ocr_uncertainty = _ocr_uncertainty_finding(
                job_id,
                display_index,
                reference_page,
                candidate_page,
            )
            if ocr_uncertainty is not None:
                findings.append(ocr_uncertainty)
            if metadata_changes and not metadata_attached:
                findings.append(
                    _metadata_change_finding(
                        job_id,
                        display_index,
                        reference_page,
                        candidate_page,
                        metadata_changes,
                    )
                )
                metadata_attached = True
            findings.sort(key=lambda finding: (-finding.risk_score, finding.finding_id))
            suggestions.extend(page_suggestions)
            cumulative_findings += len(findings)
            self._stage(
                job_id,
                StageId.SCORING_EVIDENCE,
                f"Scoring evidence on page {event_page_number}",
                round(compare_start + page_span * 0.83),
                page_number=event_page_number,
                total_pages=total_pages,
                finding_count=cumulative_findings,
                candidate_page_url=candidate_url,
                ocr_provider=provider,
                localized_region=(findings[0].bounding_box if findings else None),
            )

            page_risk = overall_score(
                [finding.risk_score for finding in findings if finding.risk_score > 0.0],
                differences.global_changed_ratio,
            )
            text_available = bool(reference_page.text.text and candidate_page.text.text)
            confidence = min(
                99.0,
                65.0 + 27.0 * alignment.quality + (7.0 if text_available else 0.0),
            )
            candidate_height, candidate_width = candidate_image.shape[:2]
            pages.append(
                PageResult(
                    page_number=display_index,
                    status=match.status,
                    reference_page_number=reference_page.page_number,
                    candidate_page_number=candidate_page.page_number,
                    risk_score=page_risk,
                    confidence_score=round(confidence, 1),
                    coverage_score=_analysis_coverage(
                        reference_page.text, candidate_page.text
                    ),
                    alignment_quality=round(alignment.quality * 100.0, 1),
                    finding_count=len(findings),
                    width=candidate_width,
                    height=candidate_height,
                    reference_image_url=_asset_url(job_id, reference_page.asset_id),
                    candidate_image_url=_asset_url(
                        job_id,
                        "candidate-page"
                        if total_pages == 1
                        else candidate_page.asset_id,
                    ),
                    ocr=_page_ocr_summary(reference_page.text, candidate_page.text),
                    findings=findings,
                )
            )
            metadata_pages.append(
                {
                    "page_number": display_index,
                    "reference_page_number": reference_page.page_number,
                    "candidate_page_number": candidate_page.page_number,
                    "reference_to_candidate_homography": alignment.matrix.tolist(),
                    "alignment_method": alignment.method,
                    "alignment_quality": alignment.quality,
                    "ocr_provider": provider,
                }
            )
            del reference_image, candidate_image, alignment, differences

        self._stage(
            job_id,
            StageId.PREPARING_RESULT,
            "Aggregating document risk",
            94,
            page_number=min(total_pages, max(1, len(pages))),
            total_pages=total_pages,
            finding_count=cumulative_findings,
            candidate_page_url=(pages[-1].candidate_image_url if pages else None),
        )
        aggregate = aggregate_page_results(pages, anomalies)
        result = self._build_result(
            job_id=job_id,
            reference=reference,
            candidate=candidate,
            comparison_mode=comparison_mode,
            reference_pages=reference_pages,
            candidate_pages=candidate_pages,
            pages=pages,
            matches=matches,
            anomalies=anomalies,
            suggestions=suggestions,
            aggregate=aggregate,
            similarities=similarities,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        (job_dir / "analysis-metadata.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "job_id": job_id,
                    "comparison_mode": comparison_mode.value,
                    "sequential_processing": True,
                    "reference_page_count": reference_count,
                    "candidate_page_count": candidate_count,
                    "pages": metadata_pages,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        self.store.update_job(
            job_id,
            state=JobState.COMPLETED,
            progress=100,
            stage=StageId.COMPLETE,
            message="Analysis complete",
            result=result,
            current_page=min(total_pages, max(1, len(pages))),
            total_pages=total_pages,
            candidate_page_url=(pages[-1].candidate_image_url if pages else None),
        )
        self.store.append_event(
            job_id,
            event_type="complete",
            stage=StageId.COMPLETE,
            page_stage=StageId.COMPLETE,
            message="Analysis complete",
            progress=100,
            page_number=min(total_pages, max(1, len(pages))),
            total_pages=total_pages,
            finding_count=result.finding_count,
            candidate_page_url=(pages[-1].candidate_image_url if pages else None),
        )

    def _prepare_page(
        self,
        job_id: str,
        assets_dir: Path,
        role: str,
        page_index: int,
        rendered: RenderedDocument,
    ) -> _PreparedPage:
        image = _normalize_image(rendered.image)
        asset_id = f"{role}-page-{page_index + 1}"
        path = assets_dir / f"{asset_id}.png"
        documents.write_png(path, image)
        self.store.register_asset(job_id, asset_id, path)
        if page_index == 0:
            self.store.register_asset(job_id, f"{role}-page", path)
        extraction = _empty_text_extraction("pending")
        height, width = image.shape[:2]
        page = _PreparedPage(
            page_number=page_index + 1,
            image_path=path,
            asset_id=asset_id,
            transform=rendered.transform,
            width=width,
            height=height,
            original_width=int(rendered.transform.original_width),
            original_height=int(rendered.transform.original_height),
            text=extraction,
            thumbnail=_page_thumbnail(image),
            layout=_layout_signature(extraction),
            heading=_heading(extraction),
        )
        del image
        return page

    @staticmethod
    def _set_page_text(page: _PreparedPage, extraction: TextExtraction) -> None:
        page.text = extraction
        page.layout = _layout_signature(extraction)
        page.heading = _heading(extraction)

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
        page_number: int,
        candidate_image: np.ndarray,
        alignment: AlignmentResult,
        differences: DifferenceResult,
        reference_text: TextExtraction,
        candidate_text: TextExtraction,
        comparison_mode: ComparisonMode,
        *,
        single_page: bool,
    ) -> tuple[list[Finding], list[RegionSuggestion]]:
        height, width = candidate_image.shape[:2]
        page_area = width * height
        findings: list[Finding] = []
        suggestions: list[RegionSuggestion] = []
        for index, region in enumerate(differences.regions[:5], start=1):
            finding_id = (
                f"finding-{index:03d}"
                if single_page
                else f"page-{page_number:02d}-finding-{index:03d}"
            )
            region_area = max(1, region.width * region.height)
            before = " | ".join(change.before for change in region.text_changes)[:160]
            after = " | ".join(change.after for change in region.text_changes)[:160]
            role, role_confidence, reason, label = _region_role(
                region,
                reference_text,
                candidate_text,
                comparison_mode,
                width,
                height,
            )
            bounding_box = _normalized_box(region, width, height)
            if comparison_mode is ComparisonMode.TEMPLATE and (
                region.text_changes or role is RegionRole.VARIABLE
            ):
                suggestions.append(
                    RegionSuggestion(
                        suggestion_id=f"suggestion-{page_number:02d}-{index:03d}",
                        page_number=page_number,
                        bounding_box=bounding_box,
                        role=role,
                        confidence_score=role_confidence,
                        reason=reason,
                        label=label,
                    )
                )

            if (
                comparison_mode is ComparisonMode.TEMPLATE
                and role is RegionRole.VARIABLE
                and not region.text_changes
            ):
                # A portrait or QR payload naturally changes most of its pixels.
                # Pixel content alone is therefore not a compositing signal.
                background_score, typography_score = 0.0, 0.0
            else:
                background_score, typography_score = _manipulation_indicators(
                    region,
                    alignment.aligned_reference,
                    candidate_image,
                    reference_text,
                    candidate_text,
                )
            layout_displacement = _layout_displacement_score(
                region,
                reference_text,
                candidate_text,
                width,
                height,
            )
            if comparison_mode is ComparisonMode.TEMPLATE and role is RegionRole.VARIABLE:
                layout_displacement = max(
                    layout_displacement,
                    _changed_text_displacement_score(region),
                )
            logo_seal_displacement = (
                0.0
                if layout_displacement > 0.0
                else _logo_seal_displacement_score(
                    region,
                    alignment.aligned_reference,
                    candidate_image,
                    reference_text,
                    candidate_text,
                )
            )
            if (
                comparison_mode is ComparisonMode.TEMPLATE
                and role is RegionRole.VARIABLE
                and not _template_variable_has_forensic_signal(
                    background_score,
                    typography_score,
                    layout_displacement,
                    logo_seal_displacement,
                )
            ):
                # Allowed values remain review suggestions/metadata.  They do
                # not create findings, evidence markers, or forensic crops.
                continue

            candidate_crop = candidate_image[
                region.y0 : region.y1, region.x0 : region.x1
            ].copy()
            reference_crop = alignment.aligned_reference[
                region.y0 : region.y1, region.x0 : region.x1
            ].copy()
            overlay = _difference_overlay(candidate_crop, differences.mask, region)
            asset_ids = (
                f"{finding_id}-candidate",
                f"{finding_id}-reference",
                f"{finding_id}-overlay",
            )
            for asset_id, image in zip(
                asset_ids, (candidate_crop, reference_crop, overlay), strict=True
            ):
                path = assets_dir / f"{asset_id}.png"
                documents.write_png(path, image)
                self.store.register_asset(job_id, asset_id, path)

            base_risk, confidence = finding_scores(
                changed_pixels=region.changed_pixels,
                region_area=region_area,
                page_area=page_area,
                mean_delta=region.mean_delta,
                alignment_quality=alignment.quality,
                has_text_change=bool(region.text_changes),
                comparison_mode=comparison_mode.value,
                region_role=role.value,
                typography_inconsistency=(
                    typography_score
                    if comparison_mode is ComparisonMode.TEMPLATE
                    else 0.0
                ),
                background_compositing=(
                    background_score
                    if comparison_mode is ComparisonMode.TEMPLATE
                    else 0.0
                ),
            )
            category, title, explanation, finding_risk = _describe_finding(
                comparison_mode=comparison_mode,
                role=role,
                has_text_change=bool(region.text_changes),
                before=before,
                after=after,
                base_risk=base_risk,
                background_score=background_score,
                typography_score=typography_score,
                layout_displacement=layout_displacement,
                logo_seal_displacement=logo_seal_displacement,
            )
            findings.append(
                Finding(
                    finding_id=finding_id,
                    page_number=page_number,
                    category=category,
                    title=title,
                    explanation=explanation,
                    bounding_box=bounding_box,
                    risk_score=finding_risk,
                    confidence_score=confidence,
                    severity=severity(finding_risk),
                    evidence_source=sorted(region.evidence_sources),
                    assets=AssetLinks(
                        candidate_crop_url=_asset_url(job_id, asset_ids[0]),
                        reference_crop_url=_asset_url(job_id, asset_ids[1]),
                        difference_overlay_url=_asset_url(job_id, asset_ids[2]),
                    ),
                    region_role=role,
                    supporting_measurements={
                        "changed_pixel_count": region.changed_pixels,
                        "region_pixel_area": region_area,
                        "changed_pixel_density": round(
                            region.changed_pixels / region_area, 5
                        ),
                        "mean_pixel_delta": round(region.mean_delta, 2),
                        "max_pixel_delta": region.max_delta,
                        "edge_changed_pixels": region.edge_changed_pixels,
                        "alignment_method": alignment.method,
                        "alignment_inlier_ratio": round(alignment.inlier_ratio, 4),
                        "text_before": before or None,
                        "text_after": after or None,
                        "region_role_confidence": role_confidence,
                        "background_compositing_score": background_score,
                        "typography_inconsistency_score": typography_score,
                        "layout_displacement_normalized": layout_displacement,
                        "logo_seal_displacement_score": logo_seal_displacement,
                    },
                )
            )
        return findings, suggestions

    def _build_unmatched_page(
        self,
        job_id: str,
        assets_dir: Path,
        page_number: int,
        match: _PageMatch,
        reference_pages: list[_PreparedPage],
        candidate_pages: list[_PreparedPage],
    ) -> PageResult:
        reference_page = (
            reference_pages[match.reference_index]
            if match.reference_index is not None
            else None
        )
        candidate_page = (
            candidate_pages[match.candidate_index]
            if match.candidate_index is not None
            else None
        )
        source = candidate_page or reference_page
        if source is None:
            raise RuntimeError("Page correspondence contained an empty match")
        anomaly_type = (
            PageAnomalyType.MISSING_PAGE
            if candidate_page is None
            else PageAnomalyType.ADDED_PAGE
        )
        finding = self._page_anomaly_finding(
            job_id,
            assets_dir,
            page_number,
            anomaly_type,
            reference_page,
            candidate_page,
        )
        return PageResult(
            page_number=page_number,
            status=match.status,
            reference_page_number=(reference_page.page_number if reference_page else None),
            candidate_page_number=(candidate_page.page_number if candidate_page else None),
            risk_score=finding.risk_score,
            confidence_score=finding.confidence_score,
            coverage_score=90.0,
            alignment_quality=0.0,
            finding_count=1,
            width=source.width,
            height=source.height,
            reference_image_url=(
                _asset_url(job_id, reference_page.asset_id) if reference_page else None
            ),
            candidate_image_url=(
                _asset_url(job_id, candidate_page.asset_id) if candidate_page else None
            ),
            ocr=_page_ocr_summary(
                reference_page.text if reference_page else None,
                candidate_page.text if candidate_page else None,
            ),
            findings=[finding],
        )

    def _build_match_anomaly_findings(
        self,
        job_id: str,
        assets_dir: Path,
        page_number: int,
        match: _PageMatch,
        reference_page: _PreparedPage,
        candidate_page: _PreparedPage,
    ) -> list[Finding]:
        types: list[PageAnomalyType] = []
        if match.status is PageStatus.REORDERED:
            types.append(PageAnomalyType.REORDERED_PAGE)
        if (match.dimension_similarity or 0.0) < 0.97:
            types.append(PageAnomalyType.DIMENSION_MISMATCH)
        return [
            self._page_anomaly_finding(
                job_id,
                assets_dir,
                page_number,
                anomaly_type,
                reference_page,
                candidate_page,
            )
            for anomaly_type in types
        ]

    def _page_anomaly_finding(
        self,
        job_id: str,
        assets_dir: Path,
        page_number: int,
        anomaly_type: PageAnomalyType,
        reference_page: _PreparedPage | None,
        candidate_page: _PreparedPage | None,
    ) -> Finding:
        title, explanation, risk, confidence = _anomaly_details(
            anomaly_type, reference_page, candidate_page
        )
        source = candidate_page or reference_page
        if source is None:
            raise RuntimeError("Cannot create evidence for an empty page anomaly")
        overlay = _read_page_image(source.image_path)
        tint = np.zeros_like(overlay)
        tint[:, :] = (25, 45, 220)
        overlay = cv2.addWeighted(overlay, 0.70, tint, 0.30, 0)
        cv2.rectangle(
            overlay,
            (2, 2),
            (max(2, overlay.shape[1] - 3), max(2, overlay.shape[0] - 3)),
            (20, 45, 240),
            max(3, round(min(overlay.shape[:2]) * 0.006)),
        )
        finding_id = f"page-{page_number:02d}-{anomaly_type.value}"
        overlay_id = f"{finding_id}-overlay"
        overlay_path = assets_dir / f"{overlay_id}.png"
        documents.write_png(overlay_path, overlay)
        self.store.register_asset(job_id, overlay_id, overlay_path)
        reference_url = (
            _asset_url(job_id, reference_page.asset_id)
            if reference_page
            else _asset_url(job_id, overlay_id)
        )
        candidate_url = (
            _asset_url(job_id, candidate_page.asset_id)
            if candidate_page
            else _asset_url(job_id, overlay_id)
        )
        return Finding(
            finding_id=finding_id,
            page_number=page_number,
            category=anomaly_type.value,
            title=title,
            explanation=explanation,
            bounding_box=BoundingBox(x=0.0, y=0.0, width=1.0, height=1.0),
            risk_score=risk,
            confidence_score=confidence,
            severity=severity(risk),
            evidence_source=["page_correspondence"],
            assets=AssetLinks(
                candidate_crop_url=candidate_url,
                reference_crop_url=reference_url,
                difference_overlay_url=_asset_url(job_id, overlay_id),
            ),
            region_role=RegionRole.FIXED,
            supporting_measurements={
                "reference_page_number": (
                    reference_page.page_number if reference_page else None
                ),
                "candidate_page_number": (
                    candidate_page.page_number if candidate_page else None
                ),
                "reference_width": (
                    reference_page.original_width if reference_page else None
                ),
                "reference_height": (
                    reference_page.original_height if reference_page else None
                ),
                "candidate_width": (
                    candidate_page.original_width if candidate_page else None
                ),
                "candidate_height": (
                    candidate_page.original_height if candidate_page else None
                ),
            },
        )

    @staticmethod
    def _build_result(
        *,
        job_id: str,
        reference: ValidatedUpload,
        candidate: ValidatedUpload,
        comparison_mode: ComparisonMode,
        reference_pages: list[_PreparedPage],
        candidate_pages: list[_PreparedPage],
        pages: list[PageResult],
        matches: list[_PageMatch],
        anomalies: list[PageOrderAnomaly],
        suggestions: list[RegionSuggestion],
        aggregate: DocumentAggregate,
        similarities: list[float],
        duration_ms: int,
    ) -> DocumentResult:
        reference_first, candidate_first = reference_pages[0], candidate_pages[0]
        reference_descriptor = DocumentDescriptor(
            filename=reference.filename,
            content_type=reference.content_type,
            sha256=reference.sha256,
            page_count=int(reference.page_count),
            width=reference_first.width,
            height=reference_first.height,
            preview_url=_asset_url(job_id, reference_first.asset_id),
            transform=reference_first.transform,
        )
        candidate_descriptor = DocumentDescriptor(
            filename=candidate.filename,
            content_type=candidate.content_type,
            sha256=candidate.sha256,
            page_count=int(candidate.page_count),
            width=candidate_first.width,
            height=candidate_first.height,
            preview_url=_asset_url(job_id, candidate_first.asset_id),
            transform=candidate_first.transform,
        )
        return DocumentResult(
            job_id=job_id,
            comparison_mode=comparison_mode,
            reference=reference_descriptor,
            candidate=candidate_descriptor,
            pages=pages,
            total_page_count=len(pages),
            reference_page_count=int(reference.page_count),
            candidate_page_count=int(candidate.page_count),
            page_correspondence=[
                PageCorrespondence(
                    reference_page_number=(
                        reference_pages[item.reference_index].page_number
                        if item.reference_index is not None
                        else None
                    ),
                    candidate_page_number=(
                        candidate_pages[item.candidate_index].page_number
                        if item.candidate_index is not None
                        else None
                    ),
                    status=item.status,
                    confidence_score=round(item.score * 100.0, 1),
                    heading_similarity=_optional_round(item.heading_similarity),
                    perceptual_similarity=_optional_round(item.perceptual_similarity),
                    dimension_similarity=_optional_round(item.dimension_similarity),
                )
                for item in matches
            ],
            page_order_anomalies=anomalies,
            region_suggestions=suggestions,
            document_aggregate=aggregate,
            overall_tampering_risk=aggregate.risk_score,
            risk_label=risk_label(aggregate.risk_score),
            assessment_confidence=aggregate.confidence_score,
            analysis_coverage=aggregate.coverage_score,
            alignment_quality=aggregate.alignment_quality,
            finding_count=aggregate.finding_count,
            processing_duration_ms=max(0, duration_ms),
            text_extraction=TextExtractionSummary(
                reference_source=_aggregate_provider(reference_pages),
                candidate_source=_aggregate_provider(candidate_pages),
                reference_characters=sum(len(page.text.text) for page in reference_pages),
                candidate_characters=sum(len(page.text.text) for page in candidate_pages),
                similarity=(
                    round(sum(similarities) / len(similarities), 6)
                    if similarities
                    else None
                ),
            ),
            generated_at=datetime.now(UTC),
        )

    def wait(self, job_id: str, timeout: float = 30.0) -> AnalysisJob:
        with self._futures_lock:
            future = self._futures.get(job_id)
        if future is not None:
            future.result(timeout=timeout)
            self._forget_future(job_id, future)
        job = self.store.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def shutdown(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=False)
        with self._futures_lock:
            self._futures.clear()


def aggregate_page_results(
    pages: list[PageResult], anomalies: list[PageOrderAnomaly]
) -> DocumentAggregate:
    """Aggregate evidence without allowing clean pages to erase a strong one."""

    risks = sorted((page.risk_score for page in pages), reverse=True)
    risk = risks[0] + min(8.0, sum(value * 0.035 for value in risks[1:])) if risks else 0.0
    matched = [
        page
        for page in pages
        if page.status in {PageStatus.MATCHED, PageStatus.REORDERED}
    ]
    return DocumentAggregate(
        risk_score=round(min(100.0, risk), 1),
        confidence_score=_mean_score([page.confidence_score for page in pages]),
        coverage_score=_mean_score([page.coverage_score for page in pages]),
        alignment_quality=_mean_score([page.alignment_quality for page in matched]),
        finding_count=sum(page.finding_count for page in pages),
        matched_page_count=len(matched),
        missing_page_count=sum(page.status is PageStatus.MISSING for page in pages),
        added_page_count=sum(page.status is PageStatus.ADDED for page in pages),
        reordered_page_count=sum(
            anomaly.anomaly_type is PageAnomalyType.REORDERED_PAGE
            for anomaly in anomalies
        ),
    )


def _render_document_page(
    upload: ValidatedUpload, page_index: int, max_dimension: int
) -> RenderedDocument:
    page_renderer = getattr(documents, "render_document_page", None)
    if callable(page_renderer):
        return page_renderer(upload, page_index, max_dimension)
    if page_index == 0:
        return documents.render_document(upload, max_dimension)
    try:
        return documents.render_document(upload, max_dimension, page_index=page_index)
    except TypeError as exc:
        raise RuntimeError("The document renderer does not support multiple pages") from exc


def _extract_page_text(
    upload: ValidatedUpload,
    rendered: RenderedDocument,
    page_index: int,
    ocr_provider_preference: str = "auto",
) -> TextExtraction:
    page_extractor = getattr(documents, "extract_page_text", None)
    if callable(page_extractor):
        try:
            return page_extractor(
                upload,
                rendered,
                page_index,
                ocr_provider_preference=ocr_provider_preference,
            )
        except Exception:
            LOGGER.warning(
                "Text extraction failed for %s page %s; visual analysis will continue",
                upload.field,
                page_index + 1,
                exc_info=True,
            )
            return _empty_text_extraction("ocr_failed")
    if page_index == 0:
        try:
            return documents.extract_text(
                upload, ocr_provider_preference=ocr_provider_preference
            )
        except Exception:
            LOGGER.warning(
                "Text extraction failed; visual analysis will continue", exc_info=True
            )
            return _empty_text_extraction("ocr_failed")
    return _empty_text_extraction("unavailable")


def _empty_text_extraction(source: str) -> TextExtraction:
    fields: dict[str, Any] = {
        "text": "",
        "words": (),
        "source": source,
        "confidence": None,
    }
    dataclass_fields = getattr(TextExtraction, "__dataclass_fields__", {})
    if "device" in dataclass_fields:
        fields["device"] = "cpu"
    if "succeeded" in dataclass_fields:
        fields["succeeded"] = False
    if "coverage" in dataclass_fields:
        fields["coverage"] = 0.0
    if "error" in dataclass_fields:
        fields["error"] = source
    return TextExtraction(**fields)


def _estimate_page_correspondence(
    reference_pages: list[_PreparedPage], candidate_pages: list[_PreparedPage]
) -> list[_PageMatch]:
    scores = [
        [_page_similarity(reference, candidate) for candidate in candidate_pages]
        for reference in reference_pages
    ]
    if len(reference_pages) == len(candidate_pages):
        count = len(reference_pages)
        if count == 1:
            return [_match_from_score(0, 0, scores[0][0], PageStatus.MATCHED)]
        assignment = _maximum_assignment([[item[0] for item in row] for row in scores])
        identity_total = sum(scores[index][index][0] for index in range(count))
        assigned_total = sum(scores[ref][candidate][0] for ref, candidate in assignment)
        clearly_reordered = (
            assigned_total >= identity_total + max(0.14, count * 0.055)
            and all(
                _is_plausible_correspondence(scores[ref][candidate])
                for ref, candidate in assignment
            )
        )
        if clearly_reordered:
            return [
                _match_from_score(
                    ref,
                    candidate,
                    scores[ref][candidate],
                    PageStatus.REORDERED if ref != candidate else PageStatus.MATCHED,
                )
                for ref, candidate in sorted(assignment, key=lambda pair: pair[1])
            ]
    return _sequence_correspondence(reference_pages, candidate_pages, scores)


def _sequence_correspondence(
    reference_pages: list[_PreparedPage],
    candidate_pages: list[_PreparedPage],
    scores: list[list[tuple[float, float | None, float, float]]],
) -> list[_PageMatch]:
    reference_count, candidate_count = len(reference_pages), len(candidate_pages)
    gap = -0.18
    table = np.zeros((reference_count + 1, candidate_count + 1), dtype=np.float64)
    trace = np.zeros((reference_count + 1, candidate_count + 1), dtype=np.int8)
    for ref in range(1, reference_count + 1):
        table[ref, 0], trace[ref, 0] = ref * gap, 1
    for candidate in range(1, candidate_count + 1):
        table[0, candidate], trace[0, candidate] = candidate * gap, 2
    for ref in range(1, reference_count + 1):
        for candidate in range(1, candidate_count + 1):
            page_score = scores[ref - 1][candidate - 1]
            similarity = page_score[0]
            choices = (
                (
                    table[ref - 1, candidate - 1] + similarity - 0.48
                    if _is_plausible_correspondence(page_score)
                    else -np.inf
                ),
                table[ref - 1, candidate] + gap,
                table[ref, candidate - 1] + gap,
            )
            direction = int(np.argmax(choices))
            table[ref, candidate], trace[ref, candidate] = choices[direction], direction
    operations: list[_PageMatch] = []
    ref, candidate = reference_count, candidate_count
    while ref or candidate:
        direction = int(trace[ref, candidate])
        if ref and candidate and direction == 0:
            operations.append(
                _match_from_score(
                    ref - 1,
                    candidate - 1,
                    scores[ref - 1][candidate - 1],
                    PageStatus.MATCHED,
                )
            )
            ref, candidate = ref - 1, candidate - 1
        elif ref and (not candidate or direction == 1):
            operations.append(
                _PageMatch(ref - 1, None, PageStatus.MISSING, 0.99, None, None, None)
            )
            ref -= 1
        else:
            operations.append(
                _PageMatch(None, candidate - 1, PageStatus.ADDED, 0.99, None, None, None)
            )
            candidate -= 1
    operations.reverse()
    return operations


def _is_plausible_correspondence(
    score: tuple[float, float | None, float, float],
) -> bool:
    """Reject weak forced pairs while retaining visually altered matching pages."""

    total, heading, perceptual, dimension = score
    if total >= PAGE_CORRESPONDENCE_MIN_SCORE:
        return True
    return bool(
        heading is not None
        and heading >= PAGE_CORRESPONDENCE_MIN_HEADING
        and perceptual >= 0.55
        and dimension >= 0.75
    )


def _maximum_assignment(scores: list[list[float]]) -> list[tuple[int, int]]:
    count = len(scores)
    states: dict[int, tuple[float, tuple[int, ...]]] = {0: (0.0, ())}
    for reference in range(count):
        next_states: dict[int, tuple[float, tuple[int, ...]]] = {}
        for mask, (total, choices) in states.items():
            for candidate in range(count):
                bit = 1 << candidate
                if mask & bit:
                    continue
                candidate_total = total + scores[reference][candidate]
                new_mask = mask | bit
                previous = next_states.get(new_mask)
                if previous is None or candidate_total > previous[0]:
                    next_states[new_mask] = (
                        candidate_total,
                        choices + (candidate,),
                    )
        states = next_states
    _, candidates = states[(1 << count) - 1]
    return list(enumerate(candidates))


def _match_from_score(
    reference_index: int,
    candidate_index: int,
    score: tuple[float, float | None, float, float],
    status: PageStatus,
) -> _PageMatch:
    return _PageMatch(
        reference_index,
        candidate_index,
        status,
        score[0],
        score[1],
        score[2],
        score[3],
    )


def _page_similarity(
    reference: _PreparedPage, candidate: _PreparedPage
) -> tuple[float, float | None, float, float]:
    heading_similarity = (
        SequenceMatcher(None, reference.heading, candidate.heading).ratio()
        if reference.heading and candidate.heading
        else None
    )
    perceptual = float(
        np.clip(
            1.0
            - np.mean(
                np.abs(
                    reference.thumbnail.astype(np.float32)
                    - candidate.thumbnail.astype(np.float32)
                )
            )
            / 255.0,
            0.0,
            1.0,
        )
    )
    dimension = _dimension_similarity(reference, candidate)
    layout = float(
        np.clip(
            1.0 - np.abs(reference.layout - candidate.layout).sum() / 2.0, 0, 1
        )
    )
    if heading_similarity is None:
        score = 0.58 * perceptual + 0.24 * dimension + 0.18 * layout
    else:
        score = (
            0.38 * perceptual
            + 0.29 * heading_similarity
            + 0.18 * dimension
            + 0.15 * layout
        )
    return (
        float(np.clip(score, 0.0, 1.0)),
        heading_similarity,
        perceptual,
        dimension,
    )


def _dimension_similarity(reference: _PreparedPage, candidate: _PreparedPage) -> float:
    reference_aspect = reference.original_width / max(reference.original_height, 1)
    candidate_aspect = candidate.original_width / max(candidate.original_height, 1)
    aspect = min(reference_aspect, candidate_aspect) / max(
        reference_aspect, candidate_aspect
    )
    width = min(reference.original_width, candidate.original_width) / max(
        reference.original_width, candidate.original_width
    )
    height = min(reference.original_height, candidate.original_height) / max(
        reference.original_height, candidate.original_height
    )
    return float(
        np.clip(0.55 * aspect + 0.225 * width + 0.225 * height, 0.0, 1.0)
    )


def _page_anomalies(
    matches: list[_PageMatch],
    reference_pages: list[_PreparedPage],
    candidate_pages: list[_PreparedPage],
) -> list[PageOrderAnomaly]:
    anomalies: list[PageOrderAnomaly] = []
    for index, match in enumerate(matches, start=1):
        reference_page = (
            reference_pages[match.reference_index]
            if match.reference_index is not None
            else None
        )
        candidate_page = (
            candidate_pages[match.candidate_index]
            if match.candidate_index is not None
            else None
        )
        anomaly_types: list[PageAnomalyType] = []
        if match.status is PageStatus.MISSING:
            anomaly_types.append(PageAnomalyType.MISSING_PAGE)
        elif match.status is PageStatus.ADDED:
            anomaly_types.append(PageAnomalyType.ADDED_PAGE)
        elif match.status is PageStatus.REORDERED:
            anomaly_types.append(PageAnomalyType.REORDERED_PAGE)
        if (
            reference_page
            and candidate_page
            and (match.dimension_similarity or 0.0) < 0.97
        ):
            anomaly_types.append(PageAnomalyType.DIMENSION_MISMATCH)
        for anomaly_type in anomaly_types:
            title, explanation, risk, confidence = _anomaly_details(
                anomaly_type, reference_page, candidate_page
            )
            anomalies.append(
                PageOrderAnomaly(
                    anomaly_id=f"anomaly-{index:02d}-{anomaly_type.value}",
                    anomaly_type=anomaly_type,
                    title=title,
                    explanation=explanation,
                    risk_score=risk,
                    confidence_score=confidence,
                    reference_page_number=(
                        reference_page.page_number if reference_page else None
                    ),
                    candidate_page_number=(
                        candidate_page.page_number if candidate_page else None
                    ),
                )
            )
    return anomalies


def _anomaly_details(
    anomaly_type: PageAnomalyType,
    reference_page: _PreparedPage | None,
    candidate_page: _PreparedPage | None,
) -> tuple[str, str, float, float]:
    if anomaly_type is PageAnomalyType.MISSING_PAGE:
        number = reference_page.page_number if reference_page else "unknown"
        return (
            "Page missing",
            f"Trusted reference page {number} has no corresponding candidate page.",
            82.0,
            99.0,
        )
    if anomaly_type is PageAnomalyType.ADDED_PAGE:
        number = candidate_page.page_number if candidate_page else "unknown"
        return (
            "Page added",
            f"Candidate page {number} has no corresponding trusted reference page.",
            78.0,
            99.0,
        )
    if anomaly_type is PageAnomalyType.REORDERED_PAGE:
        return (
            "Page reordered",
            (
                f"Candidate page {candidate_page.page_number if candidate_page else '?'} "
                f"most closely corresponds to trusted reference page "
                f"{reference_page.page_number if reference_page else '?'} instead of its index."
            ),
            68.0,
            90.0,
        )
    return (
        "Page-dimension mismatch",
        (
            "The candidate page dimensions or aspect ratio differ from the "
            "corresponding reference page."
        ),
        58.0,
        96.0,
    )


def _unmatched_message(match: _PageMatch) -> str:
    return (
        "Detected a missing candidate page"
        if match.status is PageStatus.MISSING
        else "Detected an added candidate page"
    )


def _metadata_change_finding(
    job_id: str,
    page_number: int,
    reference_page: _PreparedPage,
    candidate_page: _PreparedPage,
    changes: tuple[MetadataChange, ...],
) -> Finding:
    changed_fields = ", ".join(change.field for change in changes)
    risk = round(18.0 + min(12.0, max(0, len(changes) - 1) * 4.0), 1)
    measurements: dict[str, Any] = {
        "changed_field_count": len(changes),
        "changed_fields": changed_fields,
    }
    for change in changes:
        field_key = re.sub(r"[^a-z0-9]+", "_", change.field.casefold()).strip("_")
        measurements[f"metadata_{field_key}_reference"] = change.reference_value
        measurements[f"metadata_{field_key}_candidate"] = change.candidate_value
    return Finding(
        finding_id=f"page-{page_number:02d}-metadata-change",
        page_number=page_number,
        category="metadata_change",
        title="Available document metadata changed",
        explanation=(
            f"Bounded local metadata comparison found differences in: {changed_fields}. "
            "Metadata is supporting evidence only and is not treated as proof of tampering."
        ),
        bounding_box=BoundingBox(x=0.0, y=0.0, width=1.0, height=1.0),
        risk_score=risk,
        confidence_score=96.0,
        severity=severity(risk),
        evidence_source=["document_metadata"],
        assets=AssetLinks(
            candidate_crop_url=_asset_url(job_id, candidate_page.asset_id),
            reference_crop_url=_asset_url(job_id, reference_page.asset_id),
            difference_overlay_url=_asset_url(job_id, candidate_page.asset_id),
        ),
        region_role=RegionRole.UNKNOWN,
        supporting_measurements=measurements,
    )


def _ocr_uncertainty_finding(
    job_id: str,
    page_number: int,
    reference_page: _PreparedPage,
    candidate_page: _PreparedPage,
) -> Finding | None:
    reference_failed = not _extraction_succeeded(reference_page.text)
    candidate_failed = not _extraction_succeeded(candidate_page.text)
    if not reference_failed and not candidate_failed:
        return None
    affected = (
        "reference and candidate"
        if reference_failed and candidate_failed
        else "reference"
        if reference_failed
        else "candidate"
    )
    return Finding(
        finding_id=f"page-{page_number:02d}-ocr-uncertainty",
        page_number=page_number,
        category="ocr_uncertainty",
        title="OCR evidence unavailable",
        explanation=(
            f"OCR did not produce reliable text for the {affected} page. Visual analysis "
            "continued and analysis coverage was reduced; this is not tampering evidence."
        ),
        bounding_box=BoundingBox(x=0.0, y=0.0, width=1.0, height=1.0),
        risk_score=0.0,
        confidence_score=100.0,
        severity=severity(0.0),
        evidence_source=["ocr_provider_status", "visual_analysis_continued"],
        assets=AssetLinks(
            candidate_crop_url=_asset_url(job_id, candidate_page.asset_id),
            reference_crop_url=_asset_url(job_id, reference_page.asset_id),
            difference_overlay_url=_asset_url(job_id, candidate_page.asset_id),
        ),
        region_role=RegionRole.UNKNOWN,
        supporting_measurements={
            "reference_ocr_succeeded": not reference_failed,
            "candidate_ocr_succeeded": not candidate_failed,
            "reference_provider": _provider_name(reference_page.text),
            "candidate_provider": _provider_name(candidate_page.text),
            "reference_error": (reference_page.text.error or "")[:160],
            "candidate_error": (candidate_page.text.error or "")[:160],
        },
    )


def _layout_displacement_score(
    region: DifferenceRegion,
    reference_text: TextExtraction,
    candidate_text: TextExtraction,
    page_width: int,
    page_height: int,
) -> float:
    """Return measured movement for stable, uniquely matched text.

    Content-changing regions are classified by their text evidence instead. This
    path is deliberately conservative so ordinary OCR box jitter is not labelled
    as a layout change.
    """

    if region.text_changes or not reference_text.words or not candidate_text.words:
        return 0.0

    def unique_words(extraction: TextExtraction) -> dict[str, Any]:
        grouped: dict[str, list[Any]] = {}
        for word in extraction.words:
            token = re.sub(r"\W+", "", word.text, flags=re.UNICODE).casefold()
            if len(token) >= 3:
                grouped.setdefault(token, []).append(word)
        return {token: words[0] for token, words in grouped.items() if len(words) == 1}

    reference_words = unique_words(reference_text)
    candidate_words = unique_words(candidate_text)
    region_box = (
        region.x0 / max(page_width, 1),
        region.y0 / max(page_height, 1),
        region.x1 / max(page_width, 1),
        region.y1 / max(page_height, 1),
    )
    displacements: list[float] = []
    for token in sorted(reference_words.keys() & candidate_words.keys()):
        reference_box = reference_words[token].bbox
        candidate_box = candidate_words[token].bbox
        union = (
            min(reference_box[0], candidate_box[0]),
            min(reference_box[1], candidate_box[1]),
            max(reference_box[2], candidate_box[2]),
            max(reference_box[3], candidate_box[3]),
        )
        if not _boxes_overlap(union, region_box, padding=0.012):
            continue
        reference_width = max(1e-6, reference_box[2] - reference_box[0])
        candidate_width = max(1e-6, candidate_box[2] - candidate_box[0])
        reference_height = max(1e-6, reference_box[3] - reference_box[1])
        candidate_height = max(1e-6, candidate_box[3] - candidate_box[1])
        if not (
            0.68 <= candidate_width / reference_width <= 1.47
            and 0.68 <= candidate_height / reference_height <= 1.47
        ):
            continue
        reference_center = (
            (reference_box[0] + reference_box[2]) / 2.0,
            (reference_box[1] + reference_box[3]) / 2.0,
        )
        candidate_center = (
            (candidate_box[0] + candidate_box[2]) / 2.0,
            (candidate_box[1] + candidate_box[3]) / 2.0,
        )
        displacement = float(
            np.hypot(
                candidate_center[0] - reference_center[0],
                candidate_center[1] - reference_center[1],
            )
        )
        jitter_limit = max(0.018, 0.75 * max(reference_height, candidate_height))
        if jitter_limit <= displacement <= 0.30:
            displacements.append(displacement)
    return round(max(displacements), 4) if displacements else 0.0


def _changed_text_displacement_score(region: DifferenceRegion) -> float:
    """Measure material value movement from paired OCR boxes.

    Template values are allowed to differ in content, so the stable-token layout
    detector cannot pair them.  The comparison change already carries both
    value boxes; this uses those boxes only when the movement exceeds OCR jitter.
    """

    displacements: list[float] = []
    for change in region.text_changes:
        reference_box = change.reference_bbox
        candidate_box = change.candidate_bbox
        if reference_box is None or candidate_box is None:
            continue
        reference_height = max(1e-6, reference_box[3] - reference_box[1])
        candidate_height = max(1e-6, candidate_box[3] - candidate_box[1])
        # A legitimate field may be left-, right-, or centre-aligned.  Treat the
        # closest preserved horizontal/vertical anchor as its stable placement;
        # a true translation moves every anchor by the same amount.
        horizontal_shift = min(
            abs(candidate_box[0] - reference_box[0]),
            abs(candidate_box[2] - reference_box[2]),
            abs(
                (candidate_box[0] + candidate_box[2]) / 2.0
                - (reference_box[0] + reference_box[2]) / 2.0
            ),
        )
        vertical_shift = min(
            abs(candidate_box[1] - reference_box[1]),
            abs(candidate_box[3] - reference_box[3]),
            abs(
                (candidate_box[1] + candidate_box[3]) / 2.0
                - (reference_box[1] + reference_box[3]) / 2.0
            ),
        )
        displacement = float(np.hypot(horizontal_shift, vertical_shift))
        jitter_limit = max(0.018, 0.75 * max(reference_height, candidate_height))
        if jitter_limit <= displacement <= 0.30:
            displacements.append(displacement)
    return round(max(displacements), 4) if displacements else 0.0


def _template_variable_has_forensic_signal(
    background_score: float,
    typography_score: float,
    layout_displacement: float,
    visual_displacement: float,
) -> bool:
    return (
        background_score >= 0.08
        or typography_score >= 0.35
        or layout_displacement > 0.0
        or visual_displacement > 0.0
    )


def _logo_seal_displacement_score(
    region: DifferenceRegion,
    aligned_reference: np.ndarray,
    candidate: np.ndarray,
    reference_text: TextExtraction,
    candidate_text: TextExtraction,
) -> float:
    """Detect movement of compact, non-text, emblem-like edge geometry.

    The calculation deliberately does not attempt logo or seal identity. It only
    reports a category when translating the trusted edge mask materially aligns
    it with a similarly sized candidate edge mask in a peripheral page region.
    """

    if region.text_changes:
        return 0.0
    height, width = candidate.shape[:2]
    page_area = max(1, width * height)
    region_area = max(1, region.width * region.height)
    area_ratio = region_area / page_area
    aspect = region.width / max(region.height, 1)
    center_x = (region.x0 + region.x1) / (2.0 * max(width, 1))
    center_y = (region.y0 + region.y1) / (2.0 * max(height, 1))
    peripheral = center_x <= 0.35 or center_x >= 0.65 or center_y <= 0.32 or center_y >= 0.68
    region_box = (
        region.x0 / max(width, 1),
        region.y0 / max(height, 1),
        region.x1 / max(width, 1),
        region.y1 / max(height, 1),
    )
    if (
        not 0.0005 <= area_ratio <= 0.15
        or not 0.28 <= aspect <= 3.5
        or not peripheral
        or any(
            _boxes_overlap(word.bbox, region_box, padding=0.006)
            for word in (*reference_text.words, *candidate_text.words)
        )
    ):
        return 0.0

    reference_crop = aligned_reference[region.y0 : region.y1, region.x0 : region.x1]
    candidate_crop = candidate[region.y0 : region.y1, region.x0 : region.x1]
    if reference_crop.size == 0 or candidate_crop.size == 0:
        return 0.0
    reference_edges = cv2.Canny(
        cv2.cvtColor(reference_crop, cv2.COLOR_BGR2GRAY), 60, 150
    )
    candidate_edges = cv2.Canny(
        cv2.cvtColor(candidate_crop, cv2.COLOR_BGR2GRAY), 60, 150
    )
    reference_y, reference_x = np.nonzero(reference_edges)
    candidate_y, candidate_x = np.nonzero(candidate_edges)
    if len(reference_x) < 24 or len(candidate_x) < 24:
        return 0.0
    edge_ratio = len(candidate_x) / len(reference_x)
    if not 0.45 <= edge_ratio <= 2.2:
        return 0.0
    shift_x = int(round(float(candidate_x.mean() - reference_x.mean())))
    shift_y = int(round(float(candidate_y.mean() - reference_y.mean())))
    displacement = float(np.hypot(shift_x / max(width, 1), shift_y / max(height, 1)))
    if not 0.018 <= displacement <= 0.30:
        return 0.0

    translated = cv2.warpAffine(
        reference_edges,
        np.float32([[1.0, 0.0, shift_x], [0.0, 1.0, shift_y]]),
        (reference_edges.shape[1], reference_edges.shape[0]),
        flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    kernel = np.ones((3, 3), dtype=np.uint8)
    translated = cv2.dilate(translated, kernel)
    reference_dilated = cv2.dilate(reference_edges, kernel)
    candidate_dilated = cv2.dilate(candidate_edges, kernel)
    aligned_iou = _mask_iou(translated, candidate_dilated)
    original_iou = _mask_iou(reference_dilated, candidate_dilated)
    if aligned_iou < 0.46 or aligned_iou - original_iou < 0.20:
        return 0.0
    return round(aligned_iou, 4)


def _boxes_overlap(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
    *,
    padding: float = 0.0,
) -> bool:
    return not (
        first[2] + padding <= second[0]
        or second[2] + padding <= first[0]
        or first[3] + padding <= second[1]
        or second[3] + padding <= first[1]
    )


def _mask_iou(first: np.ndarray, second: np.ndarray) -> float:
    first_mask = first > 0
    second_mask = second > 0
    union = int(np.count_nonzero(first_mask | second_mask))
    if not union:
        return 0.0
    return float(np.count_nonzero(first_mask & second_mask) / union)


def _describe_finding(
    *,
    comparison_mode: ComparisonMode,
    role: RegionRole,
    has_text_change: bool,
    before: str,
    after: str,
    base_risk: float,
    background_score: float,
    typography_score: float,
    layout_displacement: float = 0.0,
    logo_seal_displacement: float = 0.0,
) -> tuple[str, str, str, float]:
    if comparison_mode is ComparisonMode.TEMPLATE and role is RegionRole.VARIABLE:
        if background_score >= 0.08:
            return (
                "background_compositing",
                "Background compositing indicator",
                (
                    "The variable value changed, but a coherent background disturbance "
                    "around the field is inconsistent with a clean template fill."
                ),
                round(max(78.0, base_risk), 1),
            )
        if typography_score >= 0.35:
            return (
                "typography_inconsistency",
                "Typography inconsistency",
                (
                    "The variable value changed and its character size, baseline, or "
                    "spacing differs materially from the trusted field geometry."
                ),
                round(max(72.0, base_risk), 1),
            )
        if layout_displacement > 0.0:
            return (
                "layout_displacement",
                "Variable field moved from its trusted position",
                (
                    "The field value is allowed to differ, but its measured position "
                    "moved beyond conservative OCR geometry tolerance."
                ),
                round(max(70.0, base_risk), 1),
            )
        if logo_seal_displacement > 0.0:
            return (
                "visual_region_displacement",
                "Variable visual region displaced",
                (
                    "The portrait or machine-readable payload may differ, but its "
                    "edge geometry moved materially from the trusted placement."
                ),
                round(max(70.0, base_risk), 1),
            )
        return (
            "variable_value_change",
            "Variable value changed",
            (
                f'The allowed template field changed from "{before}" to "{after}" while '
                "retaining consistent placement and background appearance."
            ),
            round(min(18.0, 8.0 + base_risk * 0.10), 1),
        )
    if (
        comparison_mode is ComparisonMode.TEMPLATE
        and role is RegionRole.FIXED
        and has_text_change
    ):
        return (
            "fixed_label_change",
            "Fixed label changed",
            (
                f'The trusted fixed text contains "{before}", while the candidate '
                f'contains "{after}". Fixed template labels are expected to remain stable.'
            ),
            round(max(76.0, base_risk), 1),
        )
    if comparison_mode is ComparisonMode.TEMPLATE and role is RegionRole.UNKNOWN:
        if has_text_change and background_score >= 0.08:
            return (
                "background_compositing",
                "Background compositing indicator",
                (
                    "The field role is uncertain, but a coherent background disturbance "
                    "is inconsistent with a clean template fill."
                ),
                round(max(78.0, base_risk), 1),
            )
        if has_text_change and typography_score >= 0.35:
            return (
                "typography_inconsistency",
                "Typography inconsistency",
                (
                    "The field role is uncertain, but character size or baseline geometry "
                    "differs materially from the trusted text."
                ),
                round(max(72.0, base_risk), 1),
            )
        if layout_displacement <= 0.0 and logo_seal_displacement <= 0.0:
            category = (
                "unclassified_content_change"
                if has_text_change
                else "unclassified_visual_change"
            )
            return (
                category,
                "Unclassified template difference",
                (
                    "This localized difference lacks enough stable semantic context to "
                    "classify it as fixed or variable. It is retained for review without "
                    "automatically treating it as critical."
                ),
                round(min(39.0, base_risk), 1),
            )
    if has_text_change:
        return (
            "text_content_change",
            "Text content differs from the trusted reference",
            (
                f'The trusted reference contains "{before}", while the questioned '
                f'document contains "{after}". The pixel comparison localizes the '
                "change to this region."
            ),
            base_risk,
        )
    if layout_displacement > 0.0:
        return (
            "layout_displacement",
            "Stable content moved from its trusted position",
            (
                "Matching text appears at a materially different page position while "
                "retaining similar box geometry. The marker covers the displaced layout region."
            ),
            base_risk,
        )
    if logo_seal_displacement > 0.0:
        return (
            "logo_seal_displacement",
            "Emblem-like graphic displaced",
            (
                "A compact non-text graphic with similar edge geometry appears at a "
                "different position. This heuristic does not identify a logo or seal; "
                "it reports only the measured visual displacement."
            ),
            base_risk,
        )
    return (
        "visual_content_change",
        "Visual content differs from the trusted reference",
        (
            "Aligned pixels and document edges differ in this localized region. "
            "The marker shows where the questioned page diverges from the trusted reference."
        ),
        base_risk,
    )


def _region_role(
    region: DifferenceRegion,
    reference_text: TextExtraction,
    candidate_text: TextExtraction,
    comparison_mode: ComparisonMode,
    page_width: int,
    page_height: int,
) -> tuple[RegionRole, float, str, str | None]:
    if comparison_mode is ComparisonMode.EXACT:
        return RegionRole.FIXED, 100.0, "Exact mode treats all content as fixed.", None
    if not region.text_changes:
        bbox = (
            region.x0 / max(page_width, 1),
            region.y0 / max(page_height, 1),
            region.x1 / max(page_width, 1),
            region.y1 / max(page_height, 1),
        )
        label = _nearby_label(candidate_text, bbox) or _nearby_label(reference_text, bbox)
        label_key = re.sub(r"[^a-z]", "", (label or "").casefold())
        if label_key in {"photo", "portrait", "qrcode", "qr"}:
            return (
                RegionRole.VARIABLE,
                92.0,
                f'The visual payload occupies the stable "{label}" field geometry.',
                label,
            )
        if label_key in {"logo", "issuerlogo", "seal", "emblem"}:
            return (
                RegionRole.FIXED,
                90.0,
                f'The visual region is identified by the stable "{label}" label.',
                label,
            )
        return (
            RegionRole.UNKNOWN,
            45.0,
            "No reliable text box overlaps this visual difference.",
            None,
        )

    # Prefer the shared OCR/layout role inference from forensics.text.
    roles = [str(getattr(change, "role", "unknown")) for change in region.text_changes]
    if roles:
        selected = (
            RegionRole.FIXED
            if "fixed" in roles
            else RegionRole.VARIABLE
            if "variable" in roles
            else RegionRole.UNKNOWN
        )
        selected_changes = [
            change
            for change in region.text_changes
            if str(getattr(change, "role", "unknown")) == selected.value
        ]
        if selected_changes:
            confidence = max(
                float(getattr(change, "role_confidence", 0.5))
                for change in selected_changes
            )
            reason = str(
                getattr(selected_changes[0], "role_reason", "OCR/layout role inference.")
            )
            label = getattr(selected_changes[0], "field_label", None)
            return selected, round(confidence * 100.0, 1), reason, label

    before = " ".join(change.before for change in region.text_changes)
    after = " ".join(change.after for change in region.text_changes)
    bbox = _combined_change_bbox(region)
    label = _nearby_label(candidate_text, bbox) or _nearby_label(reference_text, bbox)
    label_key = re.sub(r"[^a-z]", "", (label or "").casefold())
    variable_labels = {
        "name",
        "studentname",
        "candidate",
        "candidateid",
        "identifier",
        "id",
        "date",
        "issuedate",
        "dateofbirth",
        "birthdate",
        "dob",
        "address",
        "portrait",
        "photo",
        "qrcode",
        "qr",
        "result",
        "grade",
        "marks",
        "score",
    }
    if label_key in variable_labels:
        return (
            RegionRole.VARIABLE,
            94.0,
            f'The changed value occupies the stable "{label}" field geometry.',
            label,
        )
    if _looks_like_variable_value(before) and _looks_like_variable_value(after):
        return (
            RegionRole.VARIABLE,
            78.0,
            (
                "The changed text matches a practical name, date, identifier, "
                "result, or grade pattern."
            ),
            label,
        )
    return (
        RegionRole.UNKNOWN,
        52.0,
        "The changed text lacks enough stable label context for an automatic role.",
        label,
    )


def _looks_like_variable_value(value: str) -> bool:
    cleaned = value.replace("(missing)", "").replace("(removed)", "").strip(" :|")
    if not cleaned:
        return False
    if re.search(r"\b\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}\b", cleaned):
        return True
    if re.fullmatch(r"[A-Za-z]{0,6}[-/]?\d[A-Za-z0-9-/]{2,20}", cleaned):
        return True
    if cleaned.casefold() in {
        "pass",
        "fail",
        "distinction",
        "excellent",
        "pending",
    }:
        return True
    if re.fullmatch(r"[A-F][+-]?", cleaned, flags=re.IGNORECASE):
        return True
    words = cleaned.split()
    return 1 <= len(words) <= 4 and all(
        re.fullmatch(r"[A-Za-z][A-Za-z'.-]*", word) is not None for word in words
    )


def _nearby_label(
    extraction: TextExtraction, bbox: tuple[float, float, float, float]
) -> str | None:
    x0, y0, x1, _ = bbox
    candidates: list[tuple[float, str]] = []
    for word in extraction.words:
        wx0, wy0, wx1, wy1 = word.bbox
        same_line = min(bbox[3], wy1) - max(y0, wy0) > -0.012
        if same_line and wx1 <= x0 + 0.015 and x0 - wx1 <= 0.22:
            candidates.append((x0 - wx1, word.text.strip(" :")))
        below_label = wy1 <= y0 and y0 - wy1 <= 0.07
        if below_label and min(x1, wx1) - max(x0, wx0) > 0:
            candidates.append((y0 - wy1 + 0.02, word.text.strip(" :")))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1].casefold()))
    return candidates[0][1] or None


def _combined_change_bbox(region: DifferenceRegion) -> tuple[float, float, float, float]:
    if not region.text_changes:
        return 0.0, 0.0, 1.0, 1.0
    return (
        min(change.bbox[0] for change in region.text_changes),
        min(change.bbox[1] for change in region.text_changes),
        max(change.bbox[2] for change in region.text_changes),
        max(change.bbox[3] for change in region.text_changes),
    )


def _manipulation_indicators(
    region: DifferenceRegion,
    aligned_reference: np.ndarray,
    candidate: np.ndarray,
    reference_text: TextExtraction,
    candidate_text: TextExtraction,
) -> tuple[float, float]:
    reference_crop = aligned_reference[region.y0 : region.y1, region.x0 : region.x1]
    candidate_crop = candidate[region.y0 : region.y1, region.x0 : region.x1]
    if reference_crop.size == 0 or candidate_crop.size == 0:
        return 0.0, 0.0
    reference_gray = cv2.cvtColor(reference_crop, cv2.COLOR_BGR2GRAY)
    candidate_gray = cv2.cvtColor(candidate_crop, cv2.COLOR_BGR2GRAY)
    delta = cv2.absdiff(reference_gray, candidate_gray)
    ref_gradient = cv2.Laplacian(reference_gray, cv2.CV_32F)
    cand_gradient = cv2.Laplacian(candidate_gray, cv2.CV_32F)
    flat_changed = (
        (delta >= 14)
        & (np.abs(ref_gradient) <= 8)
        & (np.abs(cand_gradient) <= 8)
    )
    background_score = float(np.count_nonzero(flat_changed) / flat_changed.size)

    bbox = _combined_change_bbox(region)
    reference_words = _words_overlapping(reference_text, bbox)
    candidate_words = _words_overlapping(candidate_text, bbox)
    typography_score = 0.0
    if reference_words and candidate_words:
        reference_height = float(
            np.median([word.bbox[3] - word.bbox[1] for word in reference_words])
        )
        candidate_height = float(
            np.median([word.bbox[3] - word.bbox[1] for word in candidate_words])
        )
        if reference_height > 0 and candidate_height > 0:
            ratio = max(reference_height, candidate_height) / min(
                reference_height, candidate_height
            )
            typography_score = min(1.0, max(0.0, (ratio - 1.0) / 0.65))
        reference_baseline = float(np.median([word.bbox[3] for word in reference_words]))
        candidate_baseline = float(np.median([word.bbox[3] for word in candidate_words]))
        typography_score = max(
            typography_score,
            min(1.0, abs(reference_baseline - candidate_baseline) / 0.025),
        )
    return round(background_score, 4), round(typography_score, 4)


def _words_overlapping(
    extraction: TextExtraction, bbox: tuple[float, float, float, float]
) -> list[Any]:
    x0, y0, x1, y1 = bbox
    return [
        word
        for word in extraction.words
        if min(x1, word.bbox[2]) > max(x0, word.bbox[0])
        and min(y1, word.bbox[3]) > max(y0, word.bbox[1])
    ]


def _page_ocr_summary(
    reference: TextExtraction | None, candidate: TextExtraction | None
) -> PageOCRSummary:
    return PageOCRSummary(
        reference_provider=_provider_name(reference),
        candidate_provider=_provider_name(candidate),
        reference_device=_device_name(reference),
        candidate_device=_device_name(candidate),
        reference_confidence=_confidence_score(reference),
        candidate_confidence=_confidence_score(candidate),
        reference_characters=len(reference.text) if reference else 0,
        candidate_characters=len(candidate.text) if candidate else 0,
        reference_succeeded=_extraction_succeeded(reference),
        candidate_succeeded=_extraction_succeeded(candidate),
    )


def _analysis_coverage(reference: TextExtraction, candidate: TextExtraction) -> float:
    explicit = [_coverage_score(reference), _coverage_score(candidate)]
    if all(value is not None for value in explicit):
        minimum = min(value for value in explicit if value is not None)
        return round(70.0 + 0.30 * minimum, 1)
    if reference.text and candidate.text:
        return 100.0
    failed = "failed" in _provider_name(reference) or "failed" in _provider_name(candidate)
    return 70.0 if failed else 88.0


def _coverage_score(extraction: TextExtraction | None) -> float | None:
    if extraction is None:
        return 0.0
    value = getattr(extraction, "coverage", None)
    if value is None:
        return None
    numeric = float(value)
    return float(
        np.clip(numeric * 100.0 if numeric <= 1.0 else numeric, 0.0, 100.0)
    )


def _confidence_score(extraction: TextExtraction | None) -> float | None:
    if extraction is None or extraction.confidence is None:
        return None
    value = float(extraction.confidence)
    return round(
        float(np.clip(value * 100.0 if value <= 1.0 else value, 0.0, 100.0)), 1
    )


def _extraction_succeeded(extraction: TextExtraction | None) -> bool:
    if extraction is None:
        return False
    explicit = getattr(extraction, "succeeded", None)
    if explicit is not None:
        return bool(explicit)
    source = _provider_name(extraction).casefold()
    return bool(extraction.text.strip()) and not any(
        token in source for token in ("failed", "unavailable", "no_embedded")
    )


def _provider_name(extraction: TextExtraction | None) -> str:
    return "not_applicable" if extraction is None else str(extraction.source)


def _device_name(extraction: TextExtraction | None) -> str:
    return (
        "not_applicable"
        if extraction is None
        else str(getattr(extraction, "device", "cpu") or "cpu")
    )


def _aggregate_provider(pages: list[_PreparedPage]) -> str:
    providers = sorted({_provider_name(page.text) for page in pages})
    return providers[0] if len(providers) == 1 else "mixed:" + ",".join(providers)


def _page_thumbnail(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, (48, 64), interpolation=cv2.INTER_AREA)
    normalized = cv2.normalize(resized, None, 0, 255, cv2.NORM_MINMAX)
    return normalized.astype(np.uint8)


def _layout_signature(extraction: TextExtraction) -> np.ndarray:
    layout = np.zeros((4, 4), dtype=np.float32)
    for word in extraction.words:
        center_x = min(3, max(0, int(((word.bbox[0] + word.bbox[2]) / 2) * 4)))
        center_y = min(3, max(0, int(((word.bbox[1] + word.bbox[3]) / 2) * 4)))
        layout[center_y, center_x] += max(1, len(word.text))
    if float(layout.sum()):
        layout /= float(layout.sum())
    return layout.ravel()


def _heading(extraction: TextExtraction) -> str:
    # Include enough of the page to move past repeated letterheads. Corresponding
    # pages still compare exactly, while record titles and major section labels
    # make reordered pages distinguishable.
    return " ".join(re.findall(r"\w+", extraction.text.casefold()))[:640]


def _read_page_image(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"Could not read prepared page image {path.name}")
    return image


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
    box_width = min(
        round((region.x1 - region.x0) / width, 6), round(1.0 - x, 6)
    )
    box_height = min(
        round((region.y1 - region.y0) / height, 6), round(1.0 - y, 6)
    )
    return BoundingBox(
        x=x,
        y=y,
        width=max(box_width, 0.000001),
        height=max(box_height, 0.000001),
    )


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


def _mean_score(values: list[float]) -> float:
    return round(sum(values) / len(values), 1) if values else 0.0


def _optional_round(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def _asset_url(job_id: str, asset_id: str) -> str:
    return f"{API_PREFIX}/analyses/{job_id}/assets/{asset_id}"
