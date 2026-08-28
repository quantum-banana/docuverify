import type {
  AnalysisError,
  AdvancedEvidenceInputs,
  AnalysisJob,
  AnalysisJobCreated,
  AnalysisState,
  AnalysisWatchHandlers,
  ComparisonMode,
  CodeAssessment,
  DigitalSignatureAssessment,
  DocumentAggregate,
  DocumentDescriptor,
  DocumentResult,
  Finding,
  InvestigativeAssessment,
  LogicalConsistencyAssessment,
  MeasurementValue,
  NormalizedBoundingBox,
  OcrSummary,
  PageCorrespondence,
  PageOrderAnomaly,
  PageResult,
  ProgressEvent,
  RegionRole,
  RegionSuggestion,
  ReferenceProfileAssessment,
  SimilarityAssessment,
  MetadataAssessment,
  CodeVerificationState,
} from '../types/contracts'

const configuredBase = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? ''
const API_ROOT = `${configuredBase}/api/v1`

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const pick = (record: Record<string, unknown>, ...keys: string[]): unknown => {
  for (const key of keys) {
    if (record[key] !== undefined && record[key] !== null) return record[key]
  }
  return undefined
}

const asString = (value: unknown, fallback = ''): string =>
  typeof value === 'string' ? value : fallback

const asNumber = (value: unknown, fallback = 0): number => {
  const parsed = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

const asBoolean = (value: unknown, fallback = false): boolean =>
  typeof value === 'boolean' ? value : fallback

const asOptionalBoolean = (value: unknown): boolean | undefined =>
  typeof value === 'boolean' ? value : undefined

const asStringArray = (value: unknown): string[] =>
  Array.isArray(value) ? value.map((item) => asString(item)).filter(Boolean) : []

const clamp = (value: number, min = 0, max = 100): number => Math.min(max, Math.max(min, value))

const asScore = (value: unknown): number => {
  const score = asNumber(value)
  return clamp(score > 0 && score <= 1 ? score * 100 : score)
}

const asOptionalPageNumber = (value: unknown): number | null => {
  if (value === undefined || value === null || value === '') return null
  const pageNumber = Math.round(asNumber(value))
  return pageNumber >= 1 ? pageNumber : null
}

const normalizeIdentifier = (value: unknown, fallback = ''): string =>
  asString(value, fallback).trim().toLowerCase().replace(/[\s-]+/g, '_')

const humanizeIdentifier = (value: string): string =>
  value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())

const parseComparisonMode = (value: unknown): ComparisonMode => {
  const mode = normalizeIdentifier(value)
  if (mode === 'template' || mode === 'docuvault') return mode
  return 'exact'
}

const assetUrl = (value: unknown): string => {
  const url = asString(value)
  if (!url || /^(?:https?:|blob:|data:)/i.test(url)) return url
  if (url.startsWith('/')) return `${configuredBase}${url}`
  return `${configuredBase}/${url}`
}

const parseBoundingBox = (value: unknown): NormalizedBoundingBox => {
  let x = 0
  let y = 0
  let width = 0
  let height = 0

  if (Array.isArray(value) && value.length >= 4) {
    ;[x, y, width, height] = value.map((item) => asNumber(item))
  } else if (isRecord(value)) {
    x = asNumber(pick(value, 'x', 'left', 'x0'))
    y = asNumber(pick(value, 'y', 'top', 'y0'))
    width = asNumber(pick(value, 'width', 'w'))
    height = asNumber(pick(value, 'height', 'h'))
    if (!width && value.x1 !== undefined) width = asNumber(value.x1) - x
    if (!height && value.y1 !== undefined) height = asNumber(value.y1) - y
  }

  return {
    x: clamp(x, 0, 1),
    y: clamp(y, 0, 1),
    width: clamp(width, 0, 1),
    height: clamp(height, 0, 1),
  }
}

const parseMeasurements = (value: unknown): Record<string, MeasurementValue> => {
  if (!isRecord(value)) return {}
  return Object.fromEntries(
    Object.entries(value).filter(
      (entry): entry is [string, MeasurementValue] =>
        entry[1] === null || ['string', 'number', 'boolean'].includes(typeof entry[1]),
    ),
  )
}

const parseDocumentDescriptor = (value: unknown): DocumentDescriptor | undefined => {
  if (!isRecord(value)) return undefined
  const transform = isRecord(value.transform) ? value.transform : {}
  return {
    filename: asString(value.filename),
    content_type: asString(value.content_type),
    sha256: asString(value.sha256),
    page_count: Math.max(1, asNumber(value.page_count, 1)),
    width: Math.max(0, asNumber(value.width)),
    height: Math.max(0, asNumber(value.height)),
    preview_url: assetUrl(value.preview_url),
    transform: {
      original_width: Math.max(0, asNumber(transform.original_width)),
      original_height: Math.max(0, asNumber(transform.original_height)),
      normalized_width: Math.max(0, asNumber(transform.normalized_width)),
      normalized_height: Math.max(0, asNumber(transform.normalized_height)),
      scale_x: asNumber(transform.scale_x, 1),
      scale_y: asNumber(transform.scale_y, 1),
      orientation_degrees: asNumber(transform.orientation_degrees),
    },
  }
}

const parseOcrSummary = (value: unknown, fallback?: Record<string, unknown>): OcrSummary | undefined => {
  const ocr = isRecord(value) ? value : {}
  const context = fallback ?? {}
  const providerValue = pick(ocr, 'provider', 'ocr_provider', 'candidate_provider') ??
    pick(context, 'ocr_provider')
  const referenceProviderValue = pick(ocr, 'reference_provider')
  const sourceValue = pick(ocr, 'source', 'text_source', 'extraction_source') ??
    pick(context, 'ocr_source', 'text_source', 'extraction_source')
  const deviceValue = pick(ocr, 'device', 'execution_device', 'ocr_device', 'candidate_device') ??
    pick(context, 'ocr_device', 'execution_device')
  const referenceDeviceValue = pick(ocr, 'reference_device')
  const confidenceValue = pick(
    ocr,
    'confidence_score',
    'confidence',
    'ocr_confidence',
    'candidate_confidence',
  ) ??
    pick(context, 'ocr_confidence', 'ocr_confidence_score')
  const referenceConfidenceValue = pick(ocr, 'reference_confidence')
  const statusValue = pick(ocr, 'status', 'state', 'ocr_status') ?? pick(context, 'ocr_status')
  const succeededValue = pick(ocr, 'succeeded', 'candidate_succeeded')
  const charactersValue = pick(
    ocr,
    'character_count',
    'characters',
    'text_characters',
    'candidate_characters',
  ) ??
    pick(context, 'ocr_character_count', 'text_characters')

  if (
    providerValue === undefined && sourceValue === undefined && deviceValue === undefined &&
    confidenceValue === undefined && statusValue === undefined && charactersValue === undefined
  ) return undefined

  return {
    source: asString(
      sourceValue,
      asString(providerValue).includes('embedded') ? 'embedded_text' : 'raster_ocr',
    ),
    provider: asString(providerValue, 'unavailable'),
    device: asString(deviceValue, 'unknown'),
    confidence_score: confidenceValue === undefined || confidenceValue === null
      ? null
      : asScore(confidenceValue),
    status: asString(
      statusValue,
      typeof succeededValue === 'boolean' && !succeededValue ? 'failed' : 'completed',
    ),
    character_count: Math.max(0, Math.round(asNumber(charactersValue))),
    succeeded: typeof succeededValue === 'boolean'
      ? succeededValue
      : normalizeIdentifier(statusValue, 'completed') !== 'failed',
    reference_provider: asString(referenceProviderValue) || undefined,
    reference_device: asString(referenceDeviceValue) || undefined,
    reference_confidence_score: referenceConfidenceValue === undefined || referenceConfidenceValue === null
      ? undefined
      : asScore(referenceConfidenceValue),
  }
}

const parseRegionRole = (value: unknown): RegionRole => {
  const role = normalizeIdentifier(value, 'unknown')
  if (role === 'fixed' || role === 'variable') return role
  return 'unknown'
}

const parseRegionSuggestion = (
  value: unknown,
  index: number,
  fallbackPageNumber = 1,
): RegionSuggestion => {
  const suggestion = isRecord(value) ? value : {}
  const pageNumber = asOptionalPageNumber(pick(suggestion, 'page_number', 'page')) ?? fallbackPageNumber
  return {
    suggestion_id: asString(
      pick(suggestion, 'suggestion_id', 'region_id', 'id'),
      `suggestion-${pageNumber}-${index + 1}`,
    ),
    page_number: pageNumber,
    role: parseRegionRole(pick(suggestion, 'role', 'region_role')),
    confidence_score: asScore(pick(suggestion, 'confidence_score', 'confidence')),
    reason: asString(
      pick(suggestion, 'reason', 'explanation'),
      'Suggested from stable label and field geometry.',
    ),
    label: asString(pick(suggestion, 'label', 'field_label', 'name')) || undefined,
    bounding_box: parseBoundingBox(
      pick(suggestion, 'bounding_box', 'normalized_bbox', 'bbox', 'region'),
    ),
  }
}

const normalizePageStatus = (value: unknown): string => {
  const status = normalizeIdentifier(value, 'matched')
  const aliases: Record<string, string> = {
    ok: 'matched',
    match: 'matched',
    complete: 'completed',
    missing_page: 'missing',
    page_missing: 'missing',
    added_page: 'added',
    page_added: 'added',
    reordered_page: 'reordered',
    page_reordered: 'reordered',
    page_dimension_mismatch: 'dimension_mismatch',
    dimensions_mismatch: 'dimension_mismatch',
  }
  return aliases[status] ?? status
}

const parsePageCorrespondence = (value: unknown): PageCorrespondence => {
  const correspondence = isRecord(value) ? value : {}
  return {
    reference_page_number: asOptionalPageNumber(
      pick(correspondence, 'reference_page_number', 'reference_page', 'reference_index'),
    ),
    candidate_page_number: asOptionalPageNumber(
      pick(correspondence, 'candidate_page_number', 'candidate_page', 'candidate_index'),
    ),
    status: normalizePageStatus(
      typeof value === 'string' ? value : pick(correspondence, 'status', 'type', 'match_status'),
    ),
    similarity_score: pick(correspondence, 'similarity_score', 'similarity', 'score', 'confidence_score') === undefined
      ? null
      : asScore(pick(correspondence, 'similarity_score', 'similarity', 'score', 'confidence_score')),
    reason: asString(pick(correspondence, 'reason', 'explanation')) || undefined,
  }
}

const parsePageAnomaly = (value: unknown, index: number): PageOrderAnomaly => {
  const anomaly = isRecord(value) ? value : {}
  const rawType = typeof value === 'string'
    ? value
    : pick(anomaly, 'type', 'anomaly_type', 'category', 'status')
  const type = normalizePageStatus(rawType || 'page_anomaly')
  const referencePageNumber = asOptionalPageNumber(
    pick(anomaly, 'reference_page_number', 'reference_page'),
  )
  const candidatePageNumber = asOptionalPageNumber(
    pick(anomaly, 'candidate_page_number', 'candidate_page'),
  )
  const pageNumber = asOptionalPageNumber(pick(anomaly, 'page_number', 'page')) ??
    (type === 'missing' ? referencePageNumber : candidatePageNumber ?? referencePageNumber)
  const risk = asScore(pick(anomaly, 'risk_score', 'risk'))
  return {
    anomaly_id: asString(pick(anomaly, 'anomaly_id', 'finding_id', 'id'), `page-anomaly-${index + 1}`),
    type,
    title: asString(anomaly.title, humanizeIdentifier(type)),
    explanation: asString(
      pick(anomaly, 'explanation', 'description', 'reason'),
      'The candidate page sequence differs from the trusted reference.',
    ),
    page_number: pageNumber,
    reference_page_number: referencePageNumber,
    candidate_page_number: candidatePageNumber,
    severity: asString(
      anomaly.severity,
      risk >= 75 ? 'critical' : risk >= 50 ? 'high' : type === 'reordered' ? 'medium' : 'high',
    ),
    risk_score: risk,
    confidence_score: asScore(pick(anomaly, 'confidence_score', 'confidence')),
  }
}

export const parseFinding = (value: unknown, index = 0): Finding => {
  const finding = isRecord(value) ? value : {}
  const assets = isRecord(finding.assets) ? finding.assets : finding
  const evidence = pick(finding, 'evidence_source', 'evidence_sources')
  return {
    finding_id: asString(pick(finding, 'finding_id', 'id'), `finding-${index + 1}`),
    page_number: Math.max(1, asNumber(pick(finding, 'page_number', 'page'), 1)),
    category: asString(finding.category, 'visual_difference'),
    title: asString(finding.title, 'Document difference'),
    explanation: asString(
      pick(finding, 'explanation', 'description'),
      'This region differs from the trusted reference.',
    ),
    bounding_box: parseBoundingBox(
      pick(finding, 'bounding_box', 'candidate_bounding_box', 'normalized_bbox', 'bbox'),
    ),
    risk_score: asScore(pick(finding, 'risk_score', 'risk')),
    confidence_score: asScore(pick(finding, 'confidence_score', 'confidence')),
    severity: asString(finding.severity, 'review'),
    region_role: parseRegionRole(pick(finding, 'region_role', 'role')),
    evidence_source: Array.isArray(evidence)
      ? evidence.map((item) => asString(item)).filter(Boolean).join(', ')
      : asString(evidence, 'Visual comparison'),
    candidate_crop_url: assetUrl(pick(assets, 'candidate_crop_url', 'candidate_crop')),
    reference_crop_url: assetUrl(pick(assets, 'reference_crop_url', 'reference_crop')),
    difference_overlay_url: assetUrl(
      pick(assets, 'difference_overlay_url', 'diff_overlay_url', 'difference_overlay'),
    ),
    measurements: parseMeasurements(pick(finding, 'measurements', 'supporting_measurements')),
  }
}

const parsePage = (value: unknown, fallbackFindings: Finding[], index: number): PageResult => {
  const page = isRecord(value) ? value : {}
  const pageNumber = Math.max(1, asNumber(pick(page, 'page_number', 'page'), index + 1))
  const pageFindings = Array.isArray(page.findings)
    ? page.findings.map((finding, findingIndex) => {
        const parsed = parseFinding(finding, findingIndex)
        const rawFinding = isRecord(finding) ? finding : {}
        return pick(rawFinding, 'page_number', 'page') === undefined
          ? { ...parsed, page_number: pageNumber }
          : parsed
      })
    : fallbackFindings.filter((finding) => finding.page_number === pageNumber)
  const candidateImageUrl = assetUrl(
    pick(page, 'candidate_image_url', 'candidate_page_url', 'candidate_preview_url', 'preview_url', 'image_url'),
  )
  const referenceImageUrl = assetUrl(
    pick(page, 'reference_image_url', 'reference_page_url', 'reference_preview_url'),
  )
  const rawSuggestions = pick(
    page,
    'region_suggestions',
    'suggested_regions',
    'suggested_variable_regions',
    'variable_region_suggestions',
  )
  const regionSuggestions = Array.isArray(rawSuggestions)
    ? rawSuggestions.map((suggestion, suggestionIndex) =>
        parseRegionSuggestion(suggestion, suggestionIndex, pageNumber))
    : []
  const derivedRisk = pageFindings.reduce((highest, finding) => Math.max(highest, finding.risk_score), 0)
  const status = normalizePageStatus(pick(page, 'status', 'page_status', 'match_status'))
  const referencePageValue = page.reference_page_number !== undefined
    ? page.reference_page_number
    : page.reference_page
  const candidatePageValue = page.candidate_page_number !== undefined
    ? page.candidate_page_number
    : page.candidate_page
  return {
    page_number: pageNumber,
    width: page.width === undefined ? undefined : asNumber(page.width),
    height: page.height === undefined ? undefined : asNumber(page.height),
    candidate_image_url: candidateImageUrl,
    reference_image_url: referenceImageUrl,
    findings: pageFindings,
    status,
    reference_page_number: referencePageValue === undefined
      ? (status === 'added' ? null : pageNumber)
      : asOptionalPageNumber(referencePageValue),
    candidate_page_number: candidatePageValue === undefined
      ? (status === 'missing' ? null : pageNumber)
      : asOptionalPageNumber(candidatePageValue),
    risk_score: asScore(
      pick(page, 'risk_score', 'page_risk', 'tampering_risk', 'overall_tampering_risk') ?? derivedRisk,
    ),
    confidence_score: asScore(
      pick(page, 'confidence_score', 'page_confidence', 'assessment_confidence', 'confidence'),
    ),
    coverage_score: asScore(
      pick(page, 'coverage_score', 'page_coverage', 'analysis_coverage', 'coverage'),
    ),
    alignment_quality: asScore(pick(page, 'alignment_quality', 'page_alignment_quality')),
    finding_count: Math.max(0, Math.round(asNumber(page.finding_count, pageFindings.length))),
    ocr: parseOcrSummary(pick(page, 'ocr', 'ocr_summary', 'text_extraction'), page),
    region_suggestions: regionSuggestions,
  }
}

const optionalString = (value: unknown): string | undefined => asString(value) || undefined

const parseCapabilityTier = (value: unknown): 'metadata_only' | 'structural' | 'visual_reference' | 'cryptographic' => {
  const tier = normalizeIdentifier(value)
  if (tier === 'structural' || tier === 'visual_reference' || tier === 'cryptographic') return tier
  return 'metadata_only'
}

const parseMatchLevel = (value: unknown, score: number): 'Strong' | 'Moderate' | 'Weak' => {
  const level = asString(value).trim().toLowerCase()
  if (level === 'strong') return 'Strong'
  if (level === 'moderate') return 'Moderate'
  if (level === 'weak') return 'Weak'
  if (score >= 80) return 'Strong'
  if (score >= 60) return 'Moderate'
  return 'Weak'
}

const capabilityDescription = (tier: ReturnType<typeof parseCapabilityTier>): string => ({
  metadata_only: 'Metadata only',
  structural: 'Structure and layout',
  visual_reference: 'Trusted visual specimen',
  cryptographic: 'Cryptographically verifiable',
})[tier]

const codeStates = new Set<CodeVerificationState>([
  'DETECTED_AND_DECODED',
  'DETECTED_BUT_UNREADABLE',
  'EXPECTED_REGION_OCCUPIED_UNVERIFIED',
  'CONFIRMED_MISSING',
  'NOT_EXPECTED',
  'DECODER_UNSUPPORTED',
  'CRYPTOGRAPHIC_VERIFICATION_UNAVAILABLE',
])

const parseCodeState = (value: unknown): CodeVerificationState | undefined => {
  const state = asString(value).trim().toUpperCase() as CodeVerificationState
  return codeStates.has(state) ? state : undefined
}

const parseProfileMatch = (value: unknown) => {
  if (!isRecord(value)) return undefined
  const rawComponents = isRecord(value.component_scores) ? value.component_scores : {}
  const componentScores = Object.fromEntries(
    Object.entries(rawComponents).map(([name, score]) => [name, asScore(score)]),
  )
  const sourceUrl = asString(value.authoritative_source_url)
  const score = asScore(value.score)
  const capabilityTier = parseCapabilityTier(value.capability_tier)
  const subtype = asString(value.subtype)
  const documentFamily = asString(value.document_family, 'Unknown family')
  return {
    profile_id: asString(value.profile_id),
    display_name: asString(value.display_name) || humanizeIdentifier(subtype || documentFamily),
    issuer: asString(value.issuer, 'Unknown issuer'),
    document_family: documentFamily,
    document_category: asString(value.document_category) || humanizeIdentifier(documentFamily),
    subtype,
    version_label: optionalString(value.version_label),
    capability_tier: capabilityTier,
    match_level: parseMatchLevel(value.match_level, score),
    reference_capability: asString(value.reference_capability) || capabilityDescription(capabilityTier),
    match_reasons: asStringArray(value.match_reasons).slice(0, 4),
    provenance_kind: asString(value.provenance_kind, 'unknown'),
    provenance_assurance: asString(value.provenance_assurance, 'unknown'),
    score,
    component_scores: componentScores,
    reference_strength: asString(value.reference_strength, 'Reference strength unavailable'),
    explanation: asString(value.explanation),
    completeness: asScore(value.completeness),
    authoritative_source_url: /^https:\/\//i.test(sourceUrl) ? sourceUrl : undefined,
    visual_reference_available: asBoolean(value.visual_reference_available),
    selected_by_override: asBoolean(value.selected_by_override),
    limitations: asStringArray(value.limitations),
  }
}

const parseReferenceProfile = (value: unknown): ReferenceProfileAssessment | undefined => {
  if (!isRecord(value)) return undefined
  const selected = parseProfileMatch(value.selected_profile)
  const topMatches = Array.isArray(value.top_matches)
    ? value.top_matches.map(parseProfileMatch).filter((item): item is NonNullable<typeof item> => Boolean(item))
    : []
  const rawAsset = isRecord(value.reference_asset) ? value.reference_asset : undefined
  const rawDimensions = rawAsset && isRecord(rawAsset.dimensions) ? rawAsset.dimensions : undefined
  const assetSourceUrl = rawAsset ? asString(rawAsset.source_url) : ''
  const referenceAsset = rawAsset ? {
    page_number: Math.max(1, Math.round(asNumber(rawAsset.page_number, 1))),
    side: asString(rawAsset.side, 'front'),
    mime_type: asString(rawAsset.mime_type, 'application/octet-stream'),
    dimensions: rawDimensions ? {
      width: Math.max(1, Math.round(asNumber(rawDimensions.width, 1))),
      height: Math.max(1, Math.round(asNumber(rawDimensions.height, 1))),
    } : undefined,
    source_url: /^https:\/\//i.test(assetSourceUrl) ? assetSourceUrl : undefined,
    retrieval_date: optionalString(rawAsset.retrieval_date),
    redistribution_status: asString(rawAsset.redistribution_status, 'unspecified'),
    trust_level: asString(rawAsset.trust_level, 'unspecified'),
  } : undefined
  return {
    selected_profile: selected,
    top_matches: topMatches,
    closest_fallback_used: asBoolean(value.closest_fallback_used),
    inferred_family: optionalString(value.inferred_family),
    inferred_issuer: optionalString(value.inferred_issuer),
    reference_strength: asString(value.reference_strength, 'Reference strength unavailable'),
    explanation: asString(value.explanation),
    checked_items: asStringArray(value.checked_items),
    unverified_items: asStringArray(value.unverified_items),
    result_summary: asString(value.result_summary, asString(value.explanation)),
    reference_asset: referenceAsset,
  }
}

const parseDigitalSignature = (value: unknown): DigitalSignatureAssessment | undefined => {
  if (!isRecord(value)) return undefined
  const checks = Array.isArray(value.checks)
    ? value.checks.filter(isRecord).map((check, index) => {
        const rawCertificate = isRecord(check.certificate) ? check.certificate : undefined
        return {
          signature_index: Math.max(1, Math.round(asNumber(check.signature_index, index + 1))),
          field_name: optionalString(check.field_name),
          status: asString(check.status, 'unsupported_signature_format'),
          cryptographically_intact: asOptionalBoolean(check.cryptographically_intact),
          signer_locally_trusted: asOptionalBoolean(check.signer_locally_trusted),
          signed_content_modified: asOptionalBoolean(check.signed_content_modified),
          incremental_updates: Math.max(0, Math.round(asNumber(check.incremental_updates))),
          signing_time: optionalString(check.signing_time),
          certificate: rawCertificate ? {
            subject: optionalString(rawCertificate.subject),
            issuer: optionalString(rawCertificate.issuer),
            serial_number: optionalString(rawCertificate.serial_number),
            valid_from: optionalString(rawCertificate.valid_from),
            valid_to: optionalString(rawCertificate.valid_to),
          } : undefined,
          explanation: asString(check.explanation),
        }
      })
    : []
  return {
    status: asString(value.status, 'unsigned'),
    signature_count: Math.max(0, Math.round(asNumber(value.signature_count, checks.length))),
    trust_store: asString(value.trust_store, 'explicit_local_store'),
    checks,
    explanation: asString(value.explanation),
    limitations: asStringArray(value.limitations),
  }
}

const parseCodes = (value: unknown): CodeAssessment | undefined => {
  if (!isRecord(value)) return undefined
  const results = Array.isArray(value.results)
    ? value.results.filter(isRecord).map((code, index) => {
        const detected = asBoolean(code.detected)
        const decoded = asBoolean(code.decoded)
        const state = parseCodeState(code.state)
          ?? (decoded
            ? 'DETECTED_AND_DECODED'
            : detected
              ? 'DETECTED_BUT_UNREADABLE'
              : 'EXPECTED_REGION_OCCUPIED_UNVERIFIED')
        return {
          code_index: Math.max(1, Math.round(asNumber(code.code_index, index + 1))),
          page_number: Math.max(1, Math.round(asNumber(code.page_number, 1))),
          symbology: asString(code.symbology, 'QR'),
          bounding_box: code.bounding_box ? parseBoundingBox(code.bounding_box) : undefined,
          detected,
          decoded,
          state,
          decoder: asString(code.decoder, 'local decoder'),
          confidence_score: asScore(code.confidence_score),
          payload_summary: optionalString(code.payload_summary),
          payload_sha256: optionalString(code.payload_sha256),
          structure_valid: asOptionalBoolean(code.structure_valid),
          visible_fields_consistent: asOptionalBoolean(code.visible_fields_consistent),
          cryptographic_verification_available: asBoolean(code.cryptographic_verification_available),
          cryptographic_verification_result: asString(
            code.cryptographic_verification_result,
            'unsupported',
          ),
          structural_tampering_indicators: asStringArray(code.structural_tampering_indicators),
          explanation: asString(code.explanation),
        }
      })
    : []
  const parsedStates = Array.isArray(value.states)
    ? value.states.map(parseCodeState).filter((state): state is CodeVerificationState => Boolean(state))
    : []
  const states = [...new Set(parsedStates.length ? parsedStates : results.map((result) => result.state))]
  if (!states.length) states.push(asString(value.expected).toLowerCase() === 'not_expected'
    ? 'NOT_EXPECTED'
    : 'EXPECTED_REGION_OCCUPIED_UNVERIFIED')
  return {
    status: asString(value.status, 'not_applicable'),
    states,
    coverage_score: asScore(value.coverage_score),
    expected: asString(value.expected, 'unknown'),
    detected_count: Math.max(0, Math.round(asNumber(value.detected_count, results.length))),
    decoded_count: Math.max(0, Math.round(asNumber(value.decoded_count))),
    results,
    explanation: asString(value.explanation),
  }
}

const parseMetadataAssessment = (value: unknown): MetadataAssessment | undefined => {
  if (!isRecord(value)) return undefined
  const indicators = Array.isArray(value.indicators)
    ? value.indicators.filter(isRecord).map((indicator) => ({
        category: asString(indicator.category, 'metadata_indicator'),
        status: asString(indicator.status, 'not_applicable'),
        severity: asString(indicator.severity, 'info'),
        confidence_score: asScore(indicator.confidence_score),
        explanation: asString(indicator.explanation),
        measurements: parseMeasurements(indicator.supporting_measurements),
      }))
    : []
  return {
    status: asString(value.status, 'not_applicable'),
    indicators,
    available_fields: asStringArray(value.available_fields),
    explanation: asString(value.explanation),
    limitations: asStringArray(value.limitations),
  }
}

const parseLogicalConsistency = (value: unknown): LogicalConsistencyAssessment | undefined => {
  if (!isRecord(value)) return undefined
  const results = Array.isArray(value.results)
    ? value.results.filter(isRecord).map((rule) => {
        const rawFields = isRecord(rule.fields_used) ? rule.fields_used : {}
        const fieldsUsed = Object.fromEntries(
          Object.entries(rawFields)
            .filter(([, fieldValue]) => fieldValue === null || typeof fieldValue === 'string')
            .map(([name, fieldValue]) => [name, fieldValue as string | null]),
        )
        return {
          rule_id: asString(rule.rule_id),
          rule_version: asString(rule.rule_version),
          status: asString(rule.status, 'skipped'),
          confidence_score: asScore(rule.confidence_score),
          fields_used: fieldsUsed,
          explanation: asString(rule.explanation),
        }
      })
    : []
  return {
    status: asString(value.status, 'not_applicable'),
    passed_count: Math.max(0, Math.round(asNumber(value.passed_count))),
    failed_count: Math.max(0, Math.round(asNumber(value.failed_count))),
    skipped_count: Math.max(0, Math.round(asNumber(value.skipped_count))),
    results,
    explanation: asString(value.explanation),
  }
}

const parseSimilarity = (value: unknown): SimilarityAssessment | undefined => {
  if (!isRecord(value)) return undefined
  const evidence = Array.isArray(value.region_evidence)
    ? value.region_evidence.filter(isRecord).map((region) => ({
        page_number: Math.max(1, Math.round(asNumber(region.page_number, 1))),
        bounding_box: parseBoundingBox(region.bounding_box),
        similarity_score: asScore(region.similarity_score),
        confidence_score: asScore(region.confidence_score),
        measurements: parseMeasurements(region.measurements),
        explanation: asString(region.explanation),
      }))
    : []
  return {
    status: asString(value.status, 'not_applicable'),
    similarity_score: value.similarity_score === null || value.similarity_score === undefined
      ? undefined
      : asScore(value.similarity_score),
    confidence_score: asScore(value.confidence_score),
    coverage_score: asScore(value.coverage_score),
    closest_exemplar: optionalString(value.closest_exemplar),
    region_evidence: evidence,
    reasons: asStringArray(value.reasons),
    compositing_score: value.compositing_score === null || value.compositing_score === undefined
      ? undefined
      : asScore(value.compositing_score),
    explanation: asString(value.explanation),
    limitations: asStringArray(value.limitations),
  }
}

const parseInvestigativeAssessment = (value: unknown): InvestigativeAssessment | undefined => {
  if (!isRecord(value)) return undefined
  const dimensions = Array.isArray(value.dimensions)
    ? value.dimensions.filter(isRecord).map((dimension) => ({
        dimension: asString(dimension.dimension),
        status: asString(dimension.status),
        score: dimension.score === null || dimension.score === undefined
          ? undefined
          : asScore(dimension.score),
        evidence_count: Math.max(0, Math.round(asNumber(dimension.evidence_count))),
        explanation: asString(dimension.explanation),
      }))
    : []
  return {
    status: asString(value.status, 'limited_evidence'),
    summary: asString(value.summary),
    dimensions,
    limitations: asStringArray(value.limitations),
  }
}

export const parseDocumentResult = (value: unknown, fallbackJobId = ''): DocumentResult => {
  const result = isRecord(value) ? value : {}
  const metrics = isRecord(result.metrics) ? result.metrics : result
  const reference = parseDocumentDescriptor(result.reference)
  const candidate = parseDocumentDescriptor(result.candidate)
  const topLevelFindings = Array.isArray(result.findings)
    ? result.findings.map((finding, index) => parseFinding(finding, index))
    : []
  const rawPages = pick(result, 'pages', 'page_results')
  const pages = Array.isArray(rawPages)
    ? rawPages.map((page, index) => parsePage(page, topLevelFindings, index))
    : []
  const nestedFindings = pages.flatMap((page) => page.findings)
  const findings = topLevelFindings.length ? topLevelFindings : nestedFindings

  if (!pages.length) {
    pages.push({
      page_number: 1,
      candidate_image_url: assetUrl(
        pick(result, 'candidate_image_url', 'candidate_page_url', 'preview_url'),
      ),
      reference_image_url: assetUrl(pick(result, 'reference_image_url', 'reference_page_url')),
      findings,
      status: 'matched',
      reference_page_number: 1,
      candidate_page_number: 1,
      risk_score: findings.reduce((highest, finding) => Math.max(highest, finding.risk_score), 0),
      confidence_score: asScore(pick(metrics, 'assessment_confidence', 'confidence')),
      coverage_score: asScore(pick(metrics, 'analysis_coverage', 'coverage')),
      alignment_quality: asScore(metrics.alignment_quality),
      finding_count: findings.length,
      region_suggestions: [],
    })
  }

  const rawTopLevelSuggestions = pick(
    result,
    'region_suggestions',
    'suggested_regions',
    'suggested_variable_regions',
    'variable_region_suggestions',
  )
  const topLevelSuggestions = Array.isArray(rawTopLevelSuggestions)
    ? rawTopLevelSuggestions.map((suggestion, index) => parseRegionSuggestion(suggestion, index))
    : []
  const suggestionMap = new Map<string, RegionSuggestion>()
  for (const suggestion of [
    ...topLevelSuggestions,
    ...pages.flatMap((page) => page.region_suggestions ?? []),
  ]) suggestionMap.set(suggestion.suggestion_id, suggestion)
  const regionSuggestions = [...suggestionMap.values()]

  const rawCorrespondence = pick(result, 'page_correspondence', 'page_matches', 'correspondence')
  const pageCorrespondence = Array.isArray(rawCorrespondence)
    ? rawCorrespondence.map(parsePageCorrespondence)
    : pages.map((page) => ({
        reference_page_number: page.reference_page_number ?? null,
        candidate_page_number: page.candidate_page_number ?? null,
        status: page.status ?? 'matched',
        similarity_score: null,
      }))

  const rawAnomalies = pick(result, 'page_order_anomalies', 'page_anomalies', 'order_anomalies')
  const explicitAnomalies = Array.isArray(rawAnomalies)
    ? rawAnomalies.map(parsePageAnomaly).map((anomaly) => {
        const reviewPage = pages.find((page) => {
          if (page.status !== anomaly.type) return false
          if (anomaly.type === 'missing') {
            return page.reference_page_number === anomaly.reference_page_number
          }
          if (anomaly.type === 'added') {
            return page.candidate_page_number === anomaly.candidate_page_number
          }
          return page.reference_page_number === anomaly.reference_page_number &&
            page.candidate_page_number === anomaly.candidate_page_number
        })
        return reviewPage ? { ...anomaly, page_number: reviewPage.page_number } : anomaly
      })
    : []
  const explicitKeys = new Set(
    explicitAnomalies.map((anomaly) => `${anomaly.type}:${anomaly.page_number ?? ''}`),
  )
  const derivedAnomalies = pages
    .filter((page) => !['matched', 'completed', 'processing'].includes(page.status ?? 'matched'))
    .filter((page) => !explicitKeys.has(`${page.status}:${page.page_number}`))
    .map((page, index): PageOrderAnomaly => {
      const status = page.status ?? 'page_anomaly'
      return {
        anomaly_id: `page-status-${page.page_number}-${index + 1}`,
        type: status,
        title: status === 'dimension_mismatch'
          ? 'Page dimension mismatch'
          : `${humanizeIdentifier(status)} page`,
        explanation: 'The page does not have a normal one-to-one match with the trusted reference.',
        page_number: page.page_number,
        reference_page_number: page.reference_page_number ?? null,
        candidate_page_number: page.candidate_page_number ?? null,
        severity: status === 'reordered' ? 'medium' : 'high',
      }
    })
  const pageOrderAnomalies = [...explicitAnomalies, ...derivedAnomalies]

  const referencePageCount = Math.max(
    1,
    Math.round(asNumber(
      pick(result, 'reference_page_count', 'reference_pages'),
      reference?.page_count ?? 1,
    )),
  )
  const candidatePageCount = Math.max(
    1,
    Math.round(asNumber(
      pick(result, 'candidate_page_count', 'candidate_pages'),
      candidate?.page_count ?? 1,
    )),
  )
  const totalPageCount = Math.max(
    1,
    pages.length,
    referencePageCount,
    candidatePageCount,
    Math.round(asNumber(pick(result, 'total_page_count', 'total_pages', 'page_count'))),
  )
  const rawAggregateValue = pick(result, 'document_aggregate', 'aggregate', 'document_summary')
  const rawAggregate = isRecord(rawAggregateValue) ? rawAggregateValue : {}
  const reviewedPages = pages.filter((page) =>
    (page.finding_count ?? page.findings.length) > 0 ||
    (page.risk_score ?? 0) >= 25 ||
    !['matched', 'completed'].includes(page.status ?? 'matched'))
  const cleanPages = pages.filter((page) =>
    (page.finding_count ?? page.findings.length) === 0 &&
    (page.risk_score ?? 0) < 25 &&
    ['matched', 'completed'].includes(page.status ?? 'matched'))
  const missingPageCount = Math.max(0, Math.round(asNumber(
    pick(rawAggregate, 'missing_page_count', 'missing_pages'),
    pages.filter((page) => page.status === 'missing').length,
  )))
  const addedPageCount = Math.max(0, Math.round(asNumber(
    pick(rawAggregate, 'added_page_count', 'added_pages'),
    pages.filter((page) => page.status === 'added').length,
  )))
  const reorderedPageCount = Math.max(0, Math.round(asNumber(
    pick(rawAggregate, 'reordered_page_count', 'reordered_pages'),
    pages.filter((page) => page.status === 'reordered').length,
  )))
  const documentAggregate: DocumentAggregate = {
    total_page_count: Math.max(
      1,
      Math.round(asNumber(pick(rawAggregate, 'total_page_count', 'total_pages'), totalPageCount)),
    ),
    matched_page_count: Math.max(0, Math.round(asNumber(
      pick(rawAggregate, 'matched_page_count', 'matched_pages'),
      pages.filter((page) => ['matched', 'completed'].includes(page.status ?? 'matched')).length,
    ))),
    reviewed_page_count: Math.max(0, Math.round(asNumber(
      pick(rawAggregate, 'reviewed_page_count', 'pages_requiring_review', 'suspicious_page_count'),
      Math.max(reviewedPages.length, missingPageCount + addedPageCount + reorderedPageCount),
    ))),
    clean_page_count: Math.max(0, Math.round(asNumber(
      pick(rawAggregate, 'clean_page_count', 'clean_pages'),
      cleanPages.length,
    ))),
    anomaly_count: Math.max(0, Math.round(asNumber(
      pick(rawAggregate, 'anomaly_count', 'page_anomaly_count'),
      Math.max(pageOrderAnomalies.length, missingPageCount + addedPageCount + reorderedPageCount),
    ))),
    finding_count: Math.max(0, Math.round(asNumber(
      pick(rawAggregate, 'finding_count', 'total_findings'),
      findings.length,
    ))),
    highest_page_risk: asScore(
      pick(rawAggregate, 'highest_page_risk', 'max_page_risk') ??
        pages.reduce((highest, page) => Math.max(highest, page.risk_score ?? 0), 0),
    ),
    risk_score: asScore(pick(rawAggregate, 'risk_score', 'overall_tampering_risk')),
    confidence_score: asScore(pick(rawAggregate, 'confidence_score', 'assessment_confidence')),
    coverage_score: asScore(pick(rawAggregate, 'coverage_score', 'analysis_coverage')),
    alignment_quality: asScore(rawAggregate.alignment_quality),
    missing_page_count: missingPageCount,
    added_page_count: addedPageCount,
    reordered_page_count: reorderedPageCount,
  }

  return {
    schema_version: asString(result.schema_version, '1.0'),
    job_id: asString(pick(result, 'job_id', 'analysis_id'), fallbackJobId),
    comparison_mode: parseComparisonMode(result.comparison_mode),
    reference,
    candidate,
    overall_tampering_risk: asScore(
      pick(metrics, 'overall_tampering_risk', 'tampering_risk', 'risk_score'),
    ),
    risk_label: asString(pick(metrics, 'risk_label', 'label'), 'Tampering risk assessed'),
    assessment_confidence: asScore(pick(metrics, 'assessment_confidence', 'confidence')),
    analysis_coverage: asScore(pick(metrics, 'analysis_coverage', 'coverage')),
    alignment_quality: asScore(metrics.alignment_quality),
    finding_count: Math.max(0, asNumber(metrics.finding_count, findings.length)),
    processing_duration_ms: Math.max(
      0,
      asNumber(
        pick(metrics, 'processing_duration_ms', 'duration_ms'),
        asNumber(pick(metrics, 'processing_duration', 'duration_seconds')) * 1000,
      ),
    ),
    pages,
    findings,
    total_page_count: totalPageCount,
    reference_page_count: referencePageCount,
    candidate_page_count: candidatePageCount,
    page_correspondence: pageCorrespondence,
    page_order_anomalies: pageOrderAnomalies,
    document_aggregate: documentAggregate,
    region_suggestions: regionSuggestions,
    reference_profile: parseReferenceProfile(result.reference_profile),
    digital_signature: parseDigitalSignature(result.digital_signature),
    codes: parseCodes(result.codes),
    metadata_assessment: parseMetadataAssessment(result.metadata_assessment),
    logical_consistency: parseLogicalConsistency(result.logical_consistency),
    handwriting: parseSimilarity(result.handwriting),
    signature_similarity: parseSimilarity(result.signature_similarity),
    investigative_assessment: parseInvestigativeAssessment(result.investigative_assessment),
  }
}

const normalizeState = (value: unknown): AnalysisState => {
  const state = asString(value).toLowerCase()
  if (['complete', 'completed', 'succeeded', 'success'].includes(state)) return 'completed'
  if (['failed', 'error', 'cancelled'].includes(state)) return 'failed'
  if (['queued', 'pending', 'created'].includes(state)) return 'queued'
  return 'processing'
}

const parseError = (value: unknown, fallback = 'Analysis could not be completed.'): AnalysisError => {
  if (typeof value === 'string') return { code: 'analysis_error', message: value }
  const error = isRecord(value) ? value : {}
  return {
    code: asString(pick(error, 'code', 'error_code'), 'analysis_error'),
    message: asString(pick(error, 'message', 'detail'), fallback),
    field: asString(error.field) || undefined,
  }
}

const parseJob = (value: unknown, fallbackJobId: string): AnalysisJob => {
  const job = isRecord(value) ? value : {}
  const state = normalizeState(pick(job, 'state', 'status'))
  const rawResult = pick(job, 'result', 'document_result')
  const result = rawResult === undefined ? undefined : parseDocumentResult(rawResult, fallbackJobId)
  return {
    job_id: asString(pick(job, 'job_id', 'id'), fallbackJobId),
    state,
    progress: clamp(asNumber(job.progress)),
    current_stage: asString(pick(job, 'current_stage', 'stage_id', 'stage'), state),
    message: asString(pick(job, 'message', 'current_stage_message', 'stage_message')),
    finding_count: Math.max(
      0,
      asNumber(pick(job, 'finding_count', 'current_finding_count'), result?.finding_count ?? 0),
    ),
    candidate_page_url: assetUrl(
      pick(job, 'candidate_page_url', 'candidate_image_url', 'preview_url'),
    ) || undefined,
    current_page: asOptionalPageNumber(pick(job, 'current_page', 'page_number')) ?? undefined,
    total_pages: asOptionalPageNumber(pick(job, 'total_pages', 'total_page_count')) ?? undefined,
    page_stage: asString(pick(job, 'page_stage', 'current_page_stage')) || undefined,
    ocr_provider: asString(pick(job, 'ocr_provider', 'current_ocr_provider')) || undefined,
    ocr_device: asString(pick(job, 'ocr_device', 'execution_device')) || undefined,
    localized_region: pick(job, 'localized_region', 'region') === undefined
      ? undefined
      : parseBoundingBox(pick(job, 'localized_region', 'region')),
    result,
    error: job.error === undefined && state !== 'failed' ? undefined : parseError(job.error),
  }
}

const parseProgressEvent = (value: unknown, fallbackJobId: string): ProgressEvent => {
  const event = isRecord(value) ? value : {}
  return {
    event_id: String(pick(event, 'event_id', 'id') ?? ''),
    job_id: asString(event.job_id, fallbackJobId),
    stage_id: asString(pick(event, 'stage_id', 'current_stage', 'stage'), 'processing'),
    message: asString(pick(event, 'message', 'stage_message'), 'Analysing document'),
    progress: clamp(asNumber(event.progress)),
    page_number: Math.max(1, asNumber(pick(event, 'page_number', 'current_page'), 1)),
    total_pages: Math.max(1, asNumber(pick(event, 'total_pages', 'total_page_count'), 1)),
    timestamp: asString(event.timestamp, new Date().toISOString()),
    finding_count: Math.max(0, asNumber(pick(event, 'finding_count', 'current_finding_count'))),
    candidate_page_url: assetUrl(
      pick(event, 'candidate_page_url', 'candidate_image_url', 'thumbnail_url', 'preview_url'),
    ) || undefined,
    page_stage: asString(pick(event, 'page_stage', 'current_page_stage')) || undefined,
    ocr_provider: asString(pick(event, 'ocr_provider', 'text_provider')) || undefined,
    ocr_device: asString(pick(event, 'ocr_device', 'execution_device')) || undefined,
    localized_region: pick(event, 'localized_region', 'region', 'bounding_box') === undefined
      ? undefined
      : parseBoundingBox(pick(event, 'localized_region', 'region', 'bounding_box')),
  }
}

const request = async (path: string, init?: RequestInit): Promise<unknown> => {
  const requestUrl =
    path.startsWith('http')
      ? path
      : path.startsWith('/api/')
        ? `${configuredBase}${path}`
        : `${API_ROOT}${path}`
  const response = await fetch(requestUrl, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...init?.headers,
    },
  })
  const contentType = response.headers.get('content-type') ?? ''
  const body = contentType.includes('application/json') ? await response.json() : await response.text()
  if (!response.ok) {
    const bodyRecord = isRecord(body) ? body : {}
    const detail = pick(bodyRecord, 'detail', 'error') ?? body
    throw parseError(detail, `Request failed with status ${response.status}.`)
  }
  return body
}

const parseCreated = (value: unknown): AnalysisJobCreated => {
  const created = isRecord(value) ? value : {}
  const jobId = asString(pick(created, 'job_id', 'id'))
  if (!jobId) throw { code: 'invalid_response', message: 'The backend did not return a job ID.' }
  return {
    job_id: jobId,
    state: 'queued',
    status_url: assetUrl(
      pick(created, 'status_url', 'job_url') ?? `/api/v1/analyses/${encodeURIComponent(jobId)}`,
    ),
    event_stream_url: assetUrl(
      pick(created, 'event_stream_url', 'events_url') ??
        `/api/v1/analyses/${encodeURIComponent(jobId)}/events`,
    ),
  }
}

const appendAdvancedEvidence = (
  form: FormData,
  inputs: AdvancedEvidenceInputs = {},
) => {
  inputs.handwritingExemplars?.slice(0, 5).forEach((file) => {
    form.append('handwriting_exemplars', file)
  })
  inputs.signatureExemplars?.slice(0, 5).forEach((file) => {
    form.append('signature_exemplars', file)
  })
}

export const createAnalysis = async (
  reference: File,
  candidate: File,
  comparisonMode: ComparisonMode,
  inputs: AdvancedEvidenceInputs = {},
): Promise<AnalysisJobCreated> => {
  const form = new FormData()
  form.append('reference', reference)
  form.append('candidate', candidate)
  form.append('comparison_mode', comparisonMode)
  appendAdvancedEvidence(form, inputs)
  return parseCreated(await request('/analyses/reference', { method: 'POST', body: form }))
}

export const createAutomaticAnalysis = async (
  candidate: File,
  inputs: AdvancedEvidenceInputs = {},
): Promise<AnalysisJobCreated> => {
  const form = new FormData()
  form.append('candidate', candidate)
  appendAdvancedEvidence(form, inputs)
  if (inputs.profileOverride) form.append('profile_override', inputs.profileOverride)
  return parseCreated(await request('/analyses/automatic', { method: 'POST', body: form }))
}

export const runDemo = async (): Promise<AnalysisJobCreated> =>
  parseCreated(await request('/demo/reference', { method: 'POST' }))

export const getAnalysis = async (jobId: string, statusUrl?: string): Promise<AnalysisJob> =>
  parseJob(
    await request(statusUrl || `/analyses/${encodeURIComponent(jobId)}`, { method: 'GET' }),
    jobId,
  )

const toAnalysisError = (error: unknown): AnalysisError => {
  if (isRecord(error) && typeof error.message === 'string') {
    return {
      code: asString(error.code, 'connection_error'),
      message: error.message,
      field: asString(error.field) || undefined,
    }
  }
  return { code: 'connection_error', message: 'The analysis service could not be reached.' }
}

/**
 * Watches a job over SSE and backs it with status polling. Polling covers a
 * quickly completed job, an SSE connection that closes before its final event,
 * and environments where streaming is interrupted by a proxy.
 */
export const watchAnalysis = (
  created: AnalysisJobCreated,
  handlers: AnalysisWatchHandlers,
): (() => void) => {
  let cancelled = false
  let source: EventSource | undefined
  let reconnectTimer: ReturnType<typeof setTimeout> | undefined
  let pollTimer: ReturnType<typeof setTimeout> | undefined
  let reconnectAttempt = 0
  let lastEventId = ''
  let terminal = false

  const cleanUp = () => {
    source?.close()
    if (reconnectTimer) clearTimeout(reconnectTimer)
    if (pollTimer) clearTimeout(pollTimer)
  }

  const finish = (result: DocumentResult) => {
    if (cancelled || terminal) return
    terminal = true
    cleanUp()
    handlers.onConnection('closed')
    handlers.onComplete(result)
  }

  const fail = (error: AnalysisError) => {
    if (cancelled || terminal) return
    terminal = true
    cleanUp()
    handlers.onConnection('closed')
    handlers.onError(error)
  }

  const inspectStatus = async (): Promise<boolean> => {
    try {
      const job = await getAnalysis(created.job_id, created.status_url)
      if (job.message || job.current_stage) {
        handlers.onProgress({
          event_id: '',
          job_id: job.job_id,
          stage_id: job.current_stage,
          message: job.message || 'Analysing document',
          progress: job.progress,
          page_number: job.current_page ?? 1,
          total_pages: job.total_pages ?? 1,
          timestamp: new Date().toISOString(),
          finding_count: job.finding_count,
          candidate_page_url: job.candidate_page_url,
          page_stage: job.page_stage,
          ocr_provider: job.ocr_provider,
          ocr_device: job.ocr_device,
          localized_region: job.localized_region,
        })
      }
      if (job.state === 'completed') {
        if (!job.result) {
          fail({ code: 'missing_result', message: 'Analysis completed without a result.' })
        } else {
          finish(job.result)
        }
        return true
      }
      if (job.state === 'failed') {
        fail(job.error ?? { code: 'analysis_failed', message: 'Analysis failed.' })
        return true
      }
      return false
    } catch {
      return false
    }
  }

  const schedulePoll = () => {
    if (cancelled || terminal) return
    pollTimer = setTimeout(async () => {
      const done = await inspectStatus()
      if (!done) schedulePoll()
    }, 1800)
  }

  const handlePayload = async (raw: string, eventType: string, browserEventId = '') => {
    if (cancelled || terminal) return
    let payload: unknown
    try {
      payload = JSON.parse(raw)
    } catch {
      return
    }
    if (browserEventId) lastEventId = browserEventId
    const record = isRecord(payload) ? payload : {}
    const state = normalizeState(pick(record, 'state', 'status', 'stage_id', 'stage'))
    const normalizedType = eventType.toLowerCase()

    if (normalizedType === 'error' || normalizedType === 'failed' || state === 'failed') {
      fail(parseError(pick(record, 'error', 'detail', 'message')))
      return
    }
    if (normalizedType === 'complete' || normalizedType === 'completed' || state === 'completed') {
      const embeddedResult = pick(record, 'result', 'document_result')
      if (embeddedResult !== undefined) finish(parseDocumentResult(embeddedResult, created.job_id))
      else await inspectStatus()
      return
    }
    handlers.onProgress(parseProgressEvent(payload, created.job_id))
  }

  const connect = () => {
    if (cancelled || terminal) return
    handlers.onConnection(reconnectAttempt ? 'reconnecting' : 'connecting')
    const separator = created.event_stream_url.includes('?') ? '&' : '?'
    const resume = lastEventId ? `${separator}last_event_id=${encodeURIComponent(lastEventId)}` : ''
    source = new EventSource(`${created.event_stream_url}${resume}`)

    source.onopen = () => {
      reconnectAttempt = 0
      handlers.onConnection('live')
    }
    source.onmessage = (event) => void handlePayload(event.data, 'message', event.lastEventId)
    for (const type of ['progress', 'status', 'complete', 'completed', 'failed']) {
      source.addEventListener(type, (event) => {
        const message = event as MessageEvent<string>
        void handlePayload(message.data, type, message.lastEventId)
      })
    }
    source.onerror = (event) => {
      if (cancelled || terminal) return
      if (event instanceof MessageEvent && typeof event.data === 'string') {
        void handlePayload(event.data, 'error', event.lastEventId)
        return
      }
      source?.close()
      handlers.onConnection('polling')
      void inspectStatus().then((done) => {
        if (done || cancelled || terminal) return
        handlers.onConnection('reconnecting')
        const delay = Math.min(5000, 600 * 2 ** reconnectAttempt)
        reconnectAttempt += 1
        reconnectTimer = setTimeout(connect, delay)
      })
    }
  }

  connect()
  void inspectStatus()
  schedulePoll()

  return () => {
    cancelled = true
    cleanUp()
  }
}

export const apiInternals = { parseBoundingBox, parseProgressEvent, parseJob, toAnalysisError }
