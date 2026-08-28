/**
 * Phase 1 wire contracts mirror shared/schemas and backend Pydantic models.
 * The normalized UI model follows them below. All wire-to-view conversion stays
 * in api/client.ts so contract reconciliation never leaks into components.
 */

export type ComparisonMode = 'exact'

export type WireJobState = 'queued' | 'running' | 'completed' | 'failed'
export type RiskLabel =
  | 'Low tampering risk'
  | 'Moderate tampering risk'
  | 'High tampering risk'
  | 'Critical tampering risk'
export type Severity = 'info' | 'low' | 'medium' | 'high' | 'critical'

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
  supporting_measurements: Record<string, MeasurementValue>
}

export interface WirePageResult {
  page_number: number
  width: number
  height: number
  reference_image_url: string
  candidate_image_url: string
  findings: WireFinding[]
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
  page_count: 1
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
  schema_version: '1.0'
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
}

export interface WireProgressEvent {
  event_id: number
  job_id: string
  stage_id: StageId
  message: string
  progress: number
  page_number: 1
  total_pages: 1
  timestamp: string
  finding_count: number
  candidate_page_url?: string | null
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
  schema_version: '1.0'
  job_id: string
  state: WireJobState
  progress: number
  current_stage: StageId
  current_stage_message: string
  created_at: string
  updated_at: string
  candidate_page_url?: string | null
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
  | 'comparing_structure'
  | 'localizing_differences'
  | 'scoring_evidence'
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

export interface PageResult {
  page_number: number
  width?: number
  height?: number
  candidate_image_url: string
  reference_image_url: string
  findings: Finding[]
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
}

export interface AnalysisWatchHandlers {
  onConnection: (state: ConnectionState) => void
  onProgress: (event: ProgressEvent) => void
  onComplete: (result: DocumentResult) => void
  onError: (error: AnalysisError) => void
}
