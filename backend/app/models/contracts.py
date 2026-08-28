"""Canonical, versioned API contracts for the Phase 1 vertical slice."""

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
    supporting_measurements: dict[str, MeasurementValue] = Field(default_factory=dict)


class PageResult(ContractModel):
    page_number: Annotated[int, Field(ge=1)]
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]
    reference_image_url: str
    candidate_image_url: str
    findings: list[Finding]


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
    page_count: Literal[1]
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


class DocumentResult(ContractModel):
    schema_version: Literal["1.0"] = "1.0"
    job_id: str
    comparison_mode: Literal["exact"] = "exact"
    reference: DocumentDescriptor
    candidate: DocumentDescriptor
    pages: list[PageResult]
    overall_tampering_risk: Score
    risk_label: RiskLabel
    assessment_confidence: Score
    analysis_coverage: Score
    alignment_quality: Score
    finding_count: Annotated[int, Field(ge=0)]
    processing_duration_ms: Annotated[int, Field(ge=0)]
    text_extraction: TextExtractionSummary
    generated_at: datetime


class ProgressEvent(ContractModel):
    event_id: Annotated[int, Field(ge=1)]
    job_id: str
    stage_id: StageId
    message: str
    progress: Progress
    page_number: Literal[1] = 1
    total_pages: Literal[1] = 1
    timestamp: datetime
    finding_count: Annotated[int, Field(ge=0)] = 0
    candidate_page_url: str | None = None


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
