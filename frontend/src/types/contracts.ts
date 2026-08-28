/**
 * Wire contracts mirror the backend payload while the normalized UI model
 * stays deliberately tolerant of Phase 1 responses. All reconciliation lives
 * in api/client.ts so components consume one stable, page-aware shape.
 */

export type ComparisonMode = 'exact' | 'template'

export type WireJobState = 'queued' | 'running' | 'completed' | 'failed'
export type RiskLabel =
  | 'Low tampering risk'
  | 'Moderate tampering risk'
  | 'High tampering risk'
  | 'Critical tampering risk'
export type Severity = 'info' | 'low' | 'medium' | 'high' | 'critical'
export type RegionRole = 'fixed' | 'variable' | 'unknown'
export type PageStatus =
  | 'matched'
  | 'missing'
  | 'added'
  | 'reordered'
  | 'dimension_mismatch'
  | 'error'
  | 'completed'
  | 'processing'

export interface WireBoundingBox {
  x: number
  y: number
  width: number
  height: number
}

export interface WireAssetLinks {
  candidate_crop_url: string
  reference_crop_url: string
  difference_overlay_url: string
}

export interface WireFinding {
  finding_id: string
  page_number: number
  category: string
  title: string
  explanation: string
  bounding_box: WireBoundingBox
  risk_score: number
  confidence_score: number
  severity: Severity
  evidence_source: string[]
  assets: WireAssetLinks
  region_role?: RegionRole
  supporting_measurements: Record<string, MeasurementValue>
}

export interface WireOcrSummary {
  source?: string
  provider?: string
  device?: string
  confidence?: number | null
  status?: string
  character_count?: number
  reference_provider?: string
  candidate_provider?: string
  reference_device?: string
  candidate_device?: string
  reference_confidence?: number | null
  candidate_confidence?: number | null
  reference_characters?: number
  candidate_characters?: number
  reference_succeeded?: boolean
  candidate_succeeded?: boolean
}

export interface WireRegionSuggestion {
  suggestion_id?: string
  page_number: number
  role: RegionRole
  confidence_score?: number
  reason: string
  label?: string | null
  bounding_box: WireBoundingBox
}

export interface WirePageResult {
  page_number: number
  width?: number
  height?: number
  reference_image_url?: string | null
  candidate_image_url?: string | null
  findings: WireFinding[]
  status?: string
  reference_page_number?: number | null
  candidate_page_number?: number | null
  risk_score?: number
  confidence_score?: number
  coverage_score?: number
  alignment_quality?: number
  finding_count?: number
  ocr?: WireOcrSummary | null
  region_suggestions?: WireRegionSuggestion[]
}

export interface WireCoordinateTransform {
  original_width: number
  original_height: number
  normalized_width: number
  normalized_height: number
  scale_x: number
  scale_y: number
  orientation_degrees: 0 | 90 | 180 | 270
}

export interface WireDocumentDescriptor {
  filename: string
  content_type: string
  sha256: string
  page_count: number
  width: number
  height: number
  preview_url: string
  transform: WireCoordinateTransform
}

export interface WireTextExtractionSummary {
  reference_source: string
  candidate_source: string
  reference_characters: number
  candidate_characters: number
  similarity: number | null
}

export interface WireDocumentResult {
  schema_version: string
  job_id: string
  comparison_mode: ComparisonMode
  reference: WireDocumentDescriptor
  candidate: WireDocumentDescriptor
  pages: WirePageResult[]
  overall_tampering_risk: number
  risk_label: RiskLabel
  assessment_confidence: number
  analysis_coverage: number
  alignment_quality: number
  finding_count: number
  processing_duration_ms: number
  text_extraction: WireTextExtractionSummary
  generated_at: string
  total_page_count?: number
  reference_page_count?: number
  candidate_page_count?: number
  page_correspondence?: unknown[]
  page_order_anomalies?: unknown[]
  document_aggregate?: Record<string, unknown>
  region_suggestions?: WireRegionSuggestion[]
}

export interface WireProgressEvent {
  event_id: number
  job_id: string
  stage_id: StageId
  message: string
  progress: number
  page_number: number
  total_pages: number
  timestamp: string
  finding_count: number
  candidate_page_url?: string | null
  page_stage?: string | null
  ocr_provider?: string | null
  ocr_device?: string | null
  localized_region?: WireBoundingBox | null
}

export interface WireAnalysisError {
  code: string
  message: string
  field: string | null
  details: Record<string, unknown>
}

export interface WireCreateAnalysisResponse {
  job_id: string
  state: WireJobState
  status_url: string
  events_url: string
}

export interface WireAnalysisJob {
  schema_version: string
  job_id: string
  state: WireJobState
  progress: number
  current_stage: StageId
  current_stage_message: string
  created_at: string
  updated_at: string
  candidate_page_url?: string | null
  current_page?: number | null
  total_pages?: number | null
  page_stage?: string | null
  ocr_provider?: string | null
  result: WireDocumentResult | null
  error: WireAnalysisError | null
}

export type AnalysisState = 'queued' | 'processing' | 'completed' | 'failed'

export type ConnectionState = 'connecting' | 'live' | 'reconnecting' | 'polling' | 'closed'

export type StageId =
  | 'queued'
  | 'validating_uploads'
  | 'rendering_documents'
  | 'normalizing_pages'
  | 'aligning_reference'
  | 'extracting_text'
  | 'identifying_regions'
  | 'comparing_structure'
  | 'comparing_typography'
  | 'localizing_differences'
  | 'scoring_evidence'
  | 'aggregating_document'
  | 'preparing_result'
  | 'complete'
  | string

export interface NormalizedBoundingBox {
  x: number
  y: number
  width: number
  height: number
}

export type MeasurementValue = string | number | boolean | null

export interface Finding {
  finding_id: string
  page_number: number
  category: string
  title: string
  explanation: string
  bounding_box: NormalizedBoundingBox
  risk_score: number
  confidence_score: number
  severity: string
  region_role?: RegionRole
  evidence_source: string
  candidate_crop_url: string
  reference_crop_url: string
  difference_overlay_url: string
  measurements: Record<string, MeasurementValue>
}

export interface DocumentTransform {
  original_width: number
  original_height: number
  normalized_width: number
  normalized_height: number
  scale_x: number
  scale_y: number
  orientation_degrees: number
}

export interface DocumentDescriptor {
  filename: string
  content_type: string
  sha256: string
  page_count: number
  width: number
  height: number
  preview_url: string
  transform: DocumentTransform
}

export interface OcrSummary {
  source: string
  provider: string
  device: string
  confidence_score: number | null
  status: string
  character_count: number
  succeeded?: boolean
  reference_provider?: string
  reference_device?: string
  reference_confidence_score?: number | null
}

export interface RegionSuggestion {
  suggestion_id: string
  page_number: number
  role: RegionRole
  confidence_score: number
  reason: string
  label?: string
  bounding_box: NormalizedBoundingBox
}

export interface PageResult {
  page_number: number
  width?: number
  height?: number
  candidate_image_url: string
  reference_image_url: string
  findings: Finding[]
  status?: PageStatus | string
  reference_page_number?: number | null
  candidate_page_number?: number | null
  risk_score?: number
  confidence_score?: number
  coverage_score?: number
  alignment_quality?: number
  finding_count?: number
  ocr?: OcrSummary
  region_suggestions?: RegionSuggestion[]
}

export interface PageCorrespondence {
  reference_page_number: number | null
  candidate_page_number: number | null
  status: string
  similarity_score: number | null
  reason?: string
}

export interface PageOrderAnomaly {
  anomaly_id: string
  type: string
  title: string
  explanation: string
  page_number: number | null
  reference_page_number: number | null
  candidate_page_number: number | null
  severity: string
  risk_score?: number
  confidence_score?: number
}

export interface DocumentAggregate {
  total_page_count: number
  matched_page_count: number
  reviewed_page_count: number
  clean_page_count: number
  anomaly_count: number
  finding_count: number
  highest_page_risk: number
  risk_score?: number
  confidence_score?: number
  coverage_score?: number
  alignment_quality?: number
  missing_page_count?: number
  added_page_count?: number
  reordered_page_count?: number
}

export interface DocumentResult {
  schema_version: string
  job_id: string
  comparison_mode: ComparisonMode
  reference?: DocumentDescriptor
  candidate?: DocumentDescriptor
  overall_tampering_risk: number
  risk_label: string
  assessment_confidence: number
  analysis_coverage: number
  alignment_quality: number
  finding_count: number
  processing_duration_ms: number
  pages: PageResult[]
  findings: Finding[]
  total_page_count?: number
  reference_page_count?: number
  candidate_page_count?: number
  page_correspondence?: PageCorrespondence[]
  page_order_anomalies?: PageOrderAnomaly[]
  document_aggregate?: DocumentAggregate
  region_suggestions?: RegionSuggestion[]
}

export interface AnalysisJobCreated {
  job_id: string
  state: 'queued'
  status_url: string
  event_stream_url: string
}

export interface AnalysisError {
  code: string
  message: string
  field?: string
}

export interface AnalysisJob {
  job_id: string
  state: AnalysisState
  progress: number
  current_stage: StageId
  message: string
  finding_count: number
  candidate_page_url?: string
  current_page?: number
  total_pages?: number
  page_stage?: string
  ocr_provider?: string
  ocr_device?: string
  localized_region?: NormalizedBoundingBox
  result?: DocumentResult
  error?: AnalysisError
}

export interface ProgressEvent {
  event_id: string
  job_id: string
  stage_id: StageId
  message: string
  progress: number
  page_number: number
  total_pages: number
  timestamp: string
  finding_count: number
  candidate_page_url?: string
  page_stage?: string
  ocr_provider?: string
  ocr_device?: string
  localized_region?: NormalizedBoundingBox
}

export interface AnalysisWatchHandlers {
  onConnection: (state: ConnectionState) => void
  onProgress: (event: ProgressEvent) => void
  onComplete: (result: DocumentResult) => void
  onError: (error: AnalysisError) => void
}
