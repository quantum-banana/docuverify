"""Canonical, versioned API contracts for document analysis.

The v1 wire format is intentionally additive.  Phase 1 consumers can continue
to read the original fields while Phase 2 clients use the page-aware fields.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


UnitFloat = Annotated[float, Field(ge=0.0, le=1.0)]
Score = Annotated[float, Field(ge=0.0, le=100.0)]
Progress = Annotated[int, Field(ge=0, le=100)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ComparisonMode(StrEnum):
    EXACT = "exact"
    TEMPLATE = "template"


class PageStatus(StrEnum):
    MATCHED = "matched"
    REORDERED = "reordered"
    MISSING = "missing"
    ADDED = "added"
    FAILED = "failed"


class RegionRole(StrEnum):
    FIXED = "fixed"
    VARIABLE = "variable"
    UNKNOWN = "unknown"


class PageAnomalyType(StrEnum):
    MISSING_PAGE = "page_missing"
    ADDED_PAGE = "page_added"
    REORDERED_PAGE = "page_reordered"
    DIMENSION_MISMATCH = "page_dimension_mismatch"


class StageId(StrEnum):
    QUEUED = "queued"
    VALIDATING_UPLOADS = "validating_uploads"
    RENDERING_DOCUMENTS = "rendering_documents"
    NORMALIZING_PAGES = "normalizing_pages"
    ALIGNING_REFERENCE = "aligning_reference"
    EXTRACTING_TEXT = "extracting_text"
    COMPARING_STRUCTURE = "comparing_structure"
    LOCALIZING_DIFFERENCES = "localizing_differences"
    SCORING_EVIDENCE = "scoring_evidence"
    PREPARING_RESULT = "preparing_result"
    COMPLETE = "complete"
    FAILED = "failed"


class RiskLabel(StrEnum):
    LOW = "Low tampering risk"
    MODERATE = "Moderate tampering risk"
    HIGH = "High tampering risk"
    CRITICAL = "Critical tampering risk"


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ErrorDetail(ContractModel):
    code: str
    message: str
    field: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(ContractModel):
    error: ErrorDetail


class BoundingBox(ContractModel):
    """Candidate-page coordinates normalized to [0, 1]."""

    x: UnitFloat
    y: UnitFloat
    width: UnitFloat
    height: UnitFloat

    @field_validator("width", "height")
    @classmethod
    def positive_extent(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("bounding-box extents must be positive")
        return value

    @model_validator(mode="after")
    def contained_in_page(self) -> "BoundingBox":
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("bounding box must remain inside the normalized page")
        return self


class AssetLinks(ContractModel):
    candidate_crop_url: str
    reference_crop_url: str
    difference_overlay_url: str


MeasurementValue = str | int | float | bool | None


class Finding(ContractModel):
    finding_id: str
    page_number: Annotated[int, Field(ge=1)]
    category: str
    title: str
    explanation: str
    bounding_box: BoundingBox
    risk_score: Score
    confidence_score: Score
    severity: Severity
    evidence_source: list[str]
    assets: AssetLinks
    region_role: RegionRole = RegionRole.UNKNOWN
    supporting_measurements: dict[str, MeasurementValue] = Field(default_factory=dict)


class PageOCRSummary(ContractModel):
    reference_provider: str
    candidate_provider: str
    reference_device: str = "cpu"
    candidate_device: str = "cpu"
    reference_confidence: Score | None = None
    candidate_confidence: Score | None = None
    reference_characters: Annotated[int, Field(ge=0)] = 0
    candidate_characters: Annotated[int, Field(ge=0)] = 0
    reference_succeeded: bool = False
    candidate_succeeded: bool = False


class PageResult(ContractModel):
    page_number: Annotated[int, Field(ge=1)]
    status: PageStatus = PageStatus.MATCHED
    reference_page_number: Annotated[int, Field(ge=1)] | None = None
    candidate_page_number: Annotated[int, Field(ge=1)] | None = None
    risk_score: Score = 0.0
    confidence_score: Score = 0.0
    coverage_score: Score = 0.0
    alignment_quality: Score = 0.0
    finding_count: Annotated[int, Field(ge=0)] = 0
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]
    reference_image_url: str | None
    candidate_image_url: str | None
    ocr: PageOCRSummary | None = None
    findings: list[Finding]

    @model_validator(mode="before")
    @classmethod
    def populate_page_defaults(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        values = dict(data)
        page_number = values.get("page_number")
        values.setdefault("reference_page_number", page_number)
        values.setdefault("candidate_page_number", page_number)
        if "finding_count" not in values:
            values["finding_count"] = len(values.get("findings") or ())
        return values


class CoordinateTransform(ContractModel):
    original_width: Annotated[int, Field(gt=0)]
    original_height: Annotated[int, Field(gt=0)]
    normalized_width: Annotated[int, Field(gt=0)]
    normalized_height: Annotated[int, Field(gt=0)]
    scale_x: Annotated[float, Field(gt=0)]
    scale_y: Annotated[float, Field(gt=0)]
    orientation_degrees: Literal[0, 90, 180, 270] = 0


class DocumentDescriptor(ContractModel):
    filename: str
    content_type: str
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    page_count: Annotated[int, Field(ge=1, le=10)]
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]
    preview_url: str
    transform: CoordinateTransform


class TextExtractionSummary(ContractModel):
    reference_source: str
    candidate_source: str
    reference_characters: Annotated[int, Field(ge=0)]
    candidate_characters: Annotated[int, Field(ge=0)]
    similarity: UnitFloat | None


class PageCorrespondence(ContractModel):
    reference_page_number: Annotated[int, Field(ge=1)] | None = None
    candidate_page_number: Annotated[int, Field(ge=1)] | None = None
    status: PageStatus
    confidence_score: Score
    heading_similarity: UnitFloat | None = None
    perceptual_similarity: UnitFloat | None = None
    dimension_similarity: UnitFloat | None = None


class PageOrderAnomaly(ContractModel):
    anomaly_id: str
    anomaly_type: PageAnomalyType
    title: str
    explanation: str
    risk_score: Score
    confidence_score: Score
    reference_page_number: Annotated[int, Field(ge=1)] | None = None
    candidate_page_number: Annotated[int, Field(ge=1)] | None = None


class RegionSuggestion(ContractModel):
    suggestion_id: str
    page_number: Annotated[int, Field(ge=1)]
    bounding_box: BoundingBox
    role: RegionRole
    confidence_score: Score
    reason: str
    label: str | None = None


class DocumentAggregate(ContractModel):
    risk_score: Score
    confidence_score: Score
    coverage_score: Score
    alignment_quality: Score
    finding_count: Annotated[int, Field(ge=0)]
    matched_page_count: Annotated[int, Field(ge=0)]
    missing_page_count: Annotated[int, Field(ge=0)]
    added_page_count: Annotated[int, Field(ge=0)]
    reordered_page_count: Annotated[int, Field(ge=0)]


class DocumentResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    job_id: str
    comparison_mode: ComparisonMode = ComparisonMode.EXACT
    reference: DocumentDescriptor
    candidate: DocumentDescriptor
    pages: list[PageResult]
    # Each input remains capped at ten physical pages. A missing reference page
    # and an unrelated added candidate page occupy separate review slots, so the
    # correspondence union can contain up to twenty entries.
    total_page_count: Annotated[
        int,
        Field(
            ge=1,
            le=20,
            description=(
                "Number of ordered review slots in pages/page_correspondence; "
                "reference and candidate physical page counts remain capped at ten each."
            ),
        ),
    ] = 1
    reference_page_count: Annotated[int, Field(ge=1, le=10)] = 1
    candidate_page_count: Annotated[int, Field(ge=1, le=10)] = 1
    page_correspondence: list[PageCorrespondence] = Field(default_factory=list)
    page_order_anomalies: list[PageOrderAnomaly] = Field(default_factory=list)
    region_suggestions: list[RegionSuggestion] = Field(default_factory=list)
    document_aggregate: DocumentAggregate | None = None
    overall_tampering_risk: Score
    risk_label: RiskLabel
    assessment_confidence: Score
    analysis_coverage: Score
    alignment_quality: Score
    finding_count: Annotated[int, Field(ge=0)]
    processing_duration_ms: Annotated[int, Field(ge=0)]
    text_extraction: TextExtractionSummary
    generated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def populate_document_defaults(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        values = dict(data)
        reference = values.get("reference")
        candidate = values.get("candidate")
        reference_pages = _field_value(reference, "page_count", 1)
        candidate_pages = _field_value(candidate, "page_count", 1)
        pages = values.get("pages") or ()
        highest_page_number = max(
            (_field_value(page, "page_number", 0) for page in pages), default=0
        )
        values.setdefault("reference_page_count", reference_pages)
        values.setdefault("candidate_page_count", candidate_pages)
        values["total_page_count"] = max(
            int(values.get("total_page_count", 1)),
            reference_pages,
            candidate_pages,
            len(pages),
            highest_page_number,
        )
        return values


class ProgressEvent(ContractModel):
    event_id: Annotated[int, Field(ge=1)]
    job_id: str
    stage_id: StageId
    message: str
    progress: Progress
    page_number: Annotated[int, Field(ge=1, le=10)] = 1
    total_pages: Annotated[int, Field(ge=1, le=10)] = 1
    page_stage: StageId | None = None
    timestamp: datetime
    finding_count: Annotated[int, Field(ge=0)] = 0
    candidate_page_url: str | None = None
    ocr_provider: str | None = None
    localized_region: BoundingBox | None = None


class CreateAnalysisResponse(ContractModel):
    job_id: str
    state: JobState
    status_url: str
    events_url: str


class AnalysisJob(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    job_id: str
    state: JobState
    progress: Progress
    current_stage: StageId
    current_stage_message: str
    created_at: datetime
    updated_at: datetime
    current_page: Annotated[int, Field(ge=1, le=10)] = 1
    total_pages: Annotated[int, Field(ge=1, le=10)] = 1
    candidate_page_url: str | None = None
    result: DocumentResult | None = None
    error: ErrorDetail | None = None


class CapabilityStatus(ContractModel):
    uploads: bool
    pdf_rendering: bool
    image_rendering: bool
    alignment: bool
    visual_comparison: bool
    embedded_pdf_text: bool
    raster_ocr: bool
    sse: bool
    multi_page: bool = True
    template_comparison: bool = True


class HealthResponse(ContractModel):
    status: Literal["ok", "degraded"]
    version: str
    current_time: datetime
    capabilities: CapabilityStatus


class DiagnosticsResponse(ContractModel):
    python_version: str
    ocr_provider: str
    ocr_device: str
    opencv_version: str
    pymupdf_version: str
    numpy_version: str
    gpu_detected: bool
    backend_ready: bool
    runtime_writable: bool


def _field_value(value: Any, field: str, default: int) -> int:
    if isinstance(value, dict):
        return int(value.get(field, default))
    return int(getattr(value, field, default))
