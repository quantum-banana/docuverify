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
    DOCUVAULT = "docuvault"


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
    IDENTIFYING_DOCUMENT_FAMILY = "identifying_document_family"
    SEARCHING_TRUSTED_PROFILES = "searching_trusted_profiles"
    MATCHING_ISSUER_LAYOUT = "matching_issuer_layout"
    DECODING_CODES = "decoding_codes"
    CHECKING_DIGITAL_SIGNATURES = "checking_digital_signatures"
    INSPECTING_METADATA = "inspecting_metadata"
    VALIDATING_FIELD_CONSISTENCY = "validating_field_consistency"
    COMPARING_HANDWRITING = "comparing_handwriting"
    COMPARING_SIGNATURES = "comparing_signatures"
    AGGREGATING_EVIDENCE = "aggregating_evidence"
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


class CheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"
    SKIPPED = "skipped"
    UNSUPPORTED = "unsupported"
    NOT_APPLICABLE = "not_applicable"


class PdfSignatureStatus(StrEnum):
    VALID_TRUSTED = "cryptographically_valid_and_locally_trusted"
    VALID_UNKNOWN_TRUST = "cryptographically_valid_but_signer_trust_unknown"
    INVALID = "cryptographically_invalid"
    MODIFIED = "signed_content_modified"
    UNSIGNED = "unsigned"
    UNSUPPORTED = "unsupported_signature_format"


class ProfileCapabilityTier(StrEnum):
    METADATA_ONLY = "metadata_only"
    STRUCTURAL = "structural"
    VISUAL_REFERENCE = "visual_reference"
    CRYPTOGRAPHIC = "cryptographic"


class QREvidenceState(StrEnum):
    DETECTED_AND_DECODED = "DETECTED_AND_DECODED"
    DETECTED_BUT_UNREADABLE = "DETECTED_BUT_UNREADABLE"
    EXPECTED_REGION_OCCUPIED_UNVERIFIED = "EXPECTED_REGION_OCCUPIED_UNVERIFIED"
    CONFIRMED_MISSING = "CONFIRMED_MISSING"
    NOT_EXPECTED = "NOT_EXPECTED"
    DECODER_UNSUPPORTED = "DECODER_UNSUPPORTED"
    CRYPTOGRAPHIC_VERIFICATION_UNAVAILABLE = "CRYPTOGRAPHIC_VERIFICATION_UNAVAILABLE"


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


class ProfileReferenceAssetSummary(ContractModel):
    page_number: Annotated[int, Field(ge=1, le=10)]
    side: str
    mime_type: str
    dimensions: dict[str, int | float | str] = Field(default_factory=dict)
    source_url: str | None = None
    retrieval_date: str | None = None
    redistribution_status: str
    trust_level: str


class ProfileMatchSummary(ContractModel):
    profile_id: str
    issuer: str
    document_family: str
    subtype: str
    provenance_kind: str
    provenance_assurance: str
    score: Score
    component_scores: dict[str, Score]
    reference_strength: str
    explanation: str
    completeness: Score
    authoritative_source_url: str | None = None
    visual_reference_available: bool = False
    display_name: str = "Document profile"
    document_category: str = "Document"
    version_label: str | None = None
    capability_tier: ProfileCapabilityTier = ProfileCapabilityTier.METADATA_ONLY
    match_level: Literal["Strong", "Moderate", "Weak"] = "Weak"
    reference_capability: str = "Metadata only"
    match_reasons: list[str] = Field(default_factory=list, max_length=4)
    reference_asset: ProfileReferenceAssetSummary | None = None
    selected_by_override: bool = False
    limitations: list[str] = Field(default_factory=list)


class ReferenceProfileAssessment(ContractModel):
    selected_profile: ProfileMatchSummary | None = None
    top_matches: list[ProfileMatchSummary] = Field(default_factory=list, max_length=3)
    closest_fallback_used: bool = False
    inferred_family: str | None = None
    inferred_issuer: str | None = None
    reference_strength: str = "User-supplied unverified reference"
    explanation: str = (
        "A user-supplied reference supports comparison but has no independent issuer proof."
    )
    checked_items: list[str] = Field(default_factory=list)
    unverified_items: list[str] = Field(default_factory=list)
    result_summary: str = "The available evidence is summarized below."
    reference_asset: ProfileReferenceAssetSummary | None = None


class CertificateSummary(ContractModel):
    subject: str | None = None
    issuer: str | None = None
    serial_number: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None


class PdfSignatureCheck(ContractModel):
    signature_index: Annotated[int, Field(ge=1)]
    field_name: str | None = None
    status: PdfSignatureStatus
    cryptographically_intact: bool | None = None
    signer_locally_trusted: bool | None = None
    signed_content_modified: bool | None = None
    incremental_updates: Annotated[int, Field(ge=0)] = 0
    signing_time: datetime | None = None
    certificate: CertificateSummary | None = None
    explanation: str


class DigitalSignatureAssessment(ContractModel):
    status: PdfSignatureStatus = PdfSignatureStatus.UNSIGNED
    signature_count: Annotated[int, Field(ge=0)] = 0
    trust_store: str = "explicit_local_store"
    checks: list[PdfSignatureCheck] = Field(default_factory=list)
    explanation: str = "No embedded PDF signature was found."
    limitations: list[str] = Field(default_factory=list)


class CodeCheckResult(ContractModel):
    code_index: Annotated[int, Field(ge=1)]
    page_number: Annotated[int, Field(ge=1, le=10)]
    symbology: str
    bounding_box: BoundingBox | None = None
    detected: bool
    decoded: bool
    decoder: str
    confidence_score: Score
    payload_summary: str | None = None
    payload_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    structure_valid: bool | None = None
    visible_fields_consistent: bool | None = None
    cryptographic_verification_available: bool = False
    cryptographic_verification_result: CheckStatus = CheckStatus.UNSUPPORTED
    structural_tampering_indicators: list[str] = Field(default_factory=list)
    explanation: str
    state: QREvidenceState = QREvidenceState.DETECTED_BUT_UNREADABLE

    @model_validator(mode="before")
    @classmethod
    def populate_qr_state(cls, data: Any) -> Any:
        if not isinstance(data, dict) or data.get("state") is not None:
            return data
        values = dict(data)
        if values.get("decoded"):
            values["state"] = QREvidenceState.DETECTED_AND_DECODED
        elif values.get("detected"):
            values["state"] = QREvidenceState.DETECTED_BUT_UNREADABLE
        else:
            values["state"] = QREvidenceState.EXPECTED_REGION_OCCUPIED_UNVERIFIED
        return values


class CodeAssessment(ContractModel):
    status: CheckStatus = CheckStatus.NOT_APPLICABLE
    expected: str = "unknown"
    detected_count: Annotated[int, Field(ge=0)] = 0
    decoded_count: Annotated[int, Field(ge=0)] = 0
    results: list[CodeCheckResult] = Field(default_factory=list)
    explanation: str = "No QR or supported barcode expectation was available."
    states: list[QREvidenceState] = Field(default_factory=list)
    coverage_score: Score = 0.0

    @model_validator(mode="before")
    @classmethod
    def populate_qr_states(cls, data: Any) -> Any:
        if not isinstance(data, dict) or data.get("states") is not None:
            return data
        values = dict(data)
        values["states"] = [
            _field_value(result, "state", QREvidenceState.DETECTED_BUT_UNREADABLE)
            for result in values.get("results") or ()
        ]
        return values


class MetadataIndicator(ContractModel):
    category: str
    status: CheckStatus
    severity: Severity
    confidence_score: Score
    explanation: str
    supporting_measurements: dict[str, MeasurementValue] = Field(default_factory=dict)


class MetadataAssessment(ContractModel):
    status: CheckStatus = CheckStatus.NOT_APPLICABLE
    indicators: list[MetadataIndicator] = Field(default_factory=list)
    available_fields: list[str] = Field(default_factory=list)
    explanation: str = "Metadata was unavailable or did not support a reliable inference."
    limitations: list[str] = Field(default_factory=list)


class LogicalRuleResult(ContractModel):
    rule_id: str
    rule_version: str
    status: CheckStatus
    confidence_score: Score
    fields_used: dict[str, str | None] = Field(default_factory=dict)
    explanation: str


class LogicalConsistencyAssessment(ContractModel):
    status: CheckStatus = CheckStatus.NOT_APPLICABLE
    passed_count: Annotated[int, Field(ge=0)] = 0
    failed_count: Annotated[int, Field(ge=0)] = 0
    skipped_count: Annotated[int, Field(ge=0)] = 0
    results: list[LogicalRuleResult] = Field(default_factory=list)
    explanation: str = "No applicable profile-driven logical rules were evaluated."


class SimilarityRegionEvidence(ContractModel):
    page_number: Annotated[int, Field(ge=1, le=10)]
    bounding_box: BoundingBox
    similarity_score: Score
    confidence_score: Score
    measurements: dict[str, MeasurementValue] = Field(default_factory=dict)
    explanation: str


class SimilarityAssessment(ContractModel):
    status: CheckStatus = CheckStatus.NOT_APPLICABLE
    similarity_score: Score | None = None
    confidence_score: Score = 0.0
    coverage_score: Score = 0.0
    closest_exemplar: str | None = None
    region_evidence: list[SimilarityRegionEvidence] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    compositing_score: Score | None = None
    explanation: str
    limitations: list[str] = Field(default_factory=list)


class AssessmentDimension(ContractModel):
    dimension: str
    status: str
    score: Score | None = None
    evidence_count: Annotated[int, Field(ge=0)] = 0
    explanation: str


class InvestigativeAssessment(ContractModel):
    status: str
    summary: str
    dimensions: list[AssessmentDimension]
    limitations: list[str] = Field(default_factory=list)


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
    reference_profile: ReferenceProfileAssessment | None = None
    digital_signature: DigitalSignatureAssessment | None = None
    codes: CodeAssessment | None = None
    metadata_assessment: MetadataAssessment | None = None
    logical_consistency: LogicalConsistencyAssessment | None = None
    handwriting: SimilarityAssessment | None = None
    signature_similarity: SimilarityAssessment | None = None
    investigative_assessment: InvestigativeAssessment | None = None
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
    docuvault_profiles: bool = False
    qr_decoding: bool = False
    pdf_signature_validation: bool = False
    metadata_forensics: bool = False
    logical_rules: bool = False
    handwriting_comparison: bool = False
    signature_comparison: bool = False


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
    docuvault_profile_count: Annotated[int, Field(ge=0)] = 0
    docuvault_invalid_profile_count: Annotated[int, Field(ge=0)] = 0
    pdf_signature_provider: str = "unavailable"
    pdf_trust_store_mode: str = "explicit_local_store"


class ProfileCatalogResponse(ContractModel):
    profiles: list[ProfileMatchSummary]
    profile_count: Annotated[int, Field(ge=0)]
    enabled_count: Annotated[int, Field(ge=0)]
    invalid_count: Annotated[int, Field(ge=0)]


class ProfileStateRequest(ContractModel):
    enabled: bool


def _field_value(value: Any, field: str, default: int) -> int:
    if isinstance(value, dict):
        return int(value.get(field, default))
    return int(getattr(value, field, default))
