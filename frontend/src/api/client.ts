import type {
  AnalysisError,
  AnalysisJob,
  AnalysisJobCreated,
  AnalysisState,
  AnalysisWatchHandlers,
  ComparisonMode,
  DocumentDescriptor,
  DocumentResult,
  Finding,
  MeasurementValue,
  NormalizedBoundingBox,
  PageResult,
  ProgressEvent,
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

const clamp = (value: number, min = 0, max = 100): number => Math.min(max, Math.max(min, value))

const asScore = (value: unknown): number => {
  const score = asNumber(value)
  return clamp(score > 0 && score <= 1 ? score * 100 : score)
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
    ? page.findings.map((finding, findingIndex) => parseFinding(finding, findingIndex))
    : fallbackFindings.filter((finding) => finding.page_number === pageNumber)
  return {
    page_number: pageNumber,
    width: page.width === undefined ? undefined : asNumber(page.width),
    height: page.height === undefined ? undefined : asNumber(page.height),
    candidate_image_url: assetUrl(
      pick(page, 'candidate_image_url', 'candidate_page_url', 'preview_url', 'image_url'),
    ),
    reference_image_url: assetUrl(pick(page, 'reference_image_url', 'reference_page_url')),
    findings: pageFindings,
  }
}

export const parseDocumentResult = (value: unknown, fallbackJobId = ''): DocumentResult => {
  const result = isRecord(value) ? value : {}
  const metrics = isRecord(result.metrics) ? result.metrics : result
  const topLevelFindings = Array.isArray(result.findings)
    ? result.findings.map((finding, index) => parseFinding(finding, index))
    : []
  const pages = Array.isArray(result.pages)
    ? result.pages.map((page, index) => parsePage(page, topLevelFindings, index))
    : []
  const findings = topLevelFindings.length
    ? topLevelFindings
    : pages.flatMap((page) => page.findings)

  if (!pages.length) {
    pages.push({
      page_number: 1,
      candidate_image_url: assetUrl(
        pick(result, 'candidate_image_url', 'candidate_page_url', 'preview_url'),
      ),
      reference_image_url: assetUrl(pick(result, 'reference_image_url', 'reference_page_url')),
      findings,
    })
  }

  return {
    schema_version: asString(result.schema_version, '1.0'),
    job_id: asString(pick(result, 'job_id', 'analysis_id'), fallbackJobId),
    comparison_mode: asString(result.comparison_mode, 'exact') as ComparisonMode,
    reference: parseDocumentDescriptor(result.reference),
    candidate: parseDocumentDescriptor(result.candidate),
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
    page_number: Math.max(1, asNumber(event.page_number, 1)),
    total_pages: Math.max(1, asNumber(event.total_pages, 1)),
    timestamp: asString(event.timestamp, new Date().toISOString()),
    finding_count: Math.max(0, asNumber(pick(event, 'finding_count', 'current_finding_count'))),
    candidate_page_url: assetUrl(
      pick(event, 'candidate_page_url', 'candidate_image_url', 'preview_url'),
    ) || undefined,
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

export const createAnalysis = async (
  reference: File,
  candidate: File,
  comparisonMode: ComparisonMode,
): Promise<AnalysisJobCreated> => {
  const form = new FormData()
  form.append('reference', reference)
  form.append('candidate', candidate)
  form.append('comparison_mode', comparisonMode)
  return parseCreated(await request('/analyses/reference', { method: 'POST', body: form }))
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
          page_number: 1,
          total_pages: 1,
          timestamp: new Date().toISOString(),
          finding_count: job.finding_count,
          candidate_page_url: job.candidate_page_url,
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
