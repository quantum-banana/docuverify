import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiInternals, parseDocumentResult, runDemo, watchAnalysis } from '../api/client'
import type {
  AnalysisJobCreated,
  AnalysisWatchHandlers,
  WireDocumentResult,
} from '../types/contracts'

const backendResult = {
  schema_version: '1.0',
  job_id: 'job-contract',
  comparison_mode: 'exact',
  reference: {
    filename: 'reference.pdf',
    content_type: 'application/pdf',
    sha256: 'a'.repeat(64),
    page_count: 1,
    width: 1200,
    height: 1600,
    preview_url: '/api/v1/analyses/job-contract/assets/reference-page',
    transform: {
      original_width: 1200,
      original_height: 1600,
      normalized_width: 1200,
      normalized_height: 1600,
      scale_x: 1,
      scale_y: 1,
      orientation_degrees: 0,
    },
  },
  candidate: {
    filename: 'candidate.pdf',
    content_type: 'application/pdf',
    sha256: 'b'.repeat(64),
    page_count: 1,
    width: 1200,
    height: 1600,
    preview_url: '/api/v1/analyses/job-contract/assets/candidate-page',
    transform: {
      original_width: 1200,
      original_height: 1600,
      normalized_width: 1200,
      normalized_height: 1600,
      scale_x: 1,
      scale_y: 1,
      orientation_degrees: 0,
    },
  },
  pages: [
    {
      page_number: 1,
      width: 1200,
      height: 1600,
      reference_image_url: '/api/v1/analyses/job-contract/assets/reference-page',
      candidate_image_url: '/api/v1/analyses/job-contract/assets/candidate-page',
      findings: [
        {
          finding_id: 'finding-contract',
          page_number: 1,
          category: 'text_change',
          title: 'Changed identifier',
          explanation: 'The identifier differs from the trusted reference.',
          bounding_box: { x: 0.25, y: 0.4, width: 0.3, height: 0.08 },
          risk_score: 88,
          confidence_score: 95,
          severity: 'high',
          evidence_source: ['visual_difference', 'embedded_pdf_text'],
          assets: {
            candidate_crop_url: '/api/v1/analyses/job-contract/assets/candidate-crop',
            reference_crop_url: '/api/v1/analyses/job-contract/assets/reference-crop',
            difference_overlay_url: '/api/v1/analyses/job-contract/assets/difference-overlay',
          },
          supporting_measurements: { changed_pixel_ratio: 0.12 },
        },
      ],
    },
  ],
  overall_tampering_risk: 81,
  risk_label: 'Critical tampering risk',
  assessment_confidence: 95,
  analysis_coverage: 100,
  alignment_quality: 93,
  finding_count: 1,
  processing_duration_ms: 1450,
  text_extraction: {
    reference_source: 'embedded_pdf_text',
    candidate_source: 'embedded_pdf_text',
    reference_characters: 200,
    candidate_characters: 198,
    similarity: 0.94,
  },
  generated_at: '2026-08-28T07:30:00Z',
} satisfies WireDocumentResult

const jsonResponse = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })

class FakeEventSource {
  static instances: FakeEventSource[] = []
  readonly url: string
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent<string>) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  closed = false
  listeners = new Map<string, EventListenerOrEventListenerObject[]>()

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener])
  }

  close() {
    this.closed = true
  }
}

describe('Phase 1 API contract and recovery behavior', () => {
  beforeEach(() => {
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('maps the backend create response and nested finding assets into the UI contract', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        job_id: 'job-contract',
        state: 'queued',
        status_url: '/api/v1/analyses/job-contract',
        events_url: '/api/v1/analyses/job-contract/events',
      }, 202),
    )
    vi.stubGlobal('fetch', fetchMock)

    const created = await runDemo()
    const parsed = parseDocumentResult(backendResult)

    expect(fetchMock).toHaveBeenCalledWith('/api/v1/demo/reference', expect.objectContaining({ method: 'POST' }))
    expect(created.event_stream_url).toBe('/api/v1/analyses/job-contract/events')
    expect(parsed.findings[0].candidate_crop_url).toBe(
      '/api/v1/analyses/job-contract/assets/candidate-crop',
    )
    expect(parsed.findings[0].evidence_source).toBe('visual_difference, embedded_pdf_text')
    expect(parsed.findings[0].measurements.changed_pixel_ratio).toBe(0.12)
    expect(parsed.pages[0].candidate_image_url).toContain('/assets/candidate-page')
  })

  it('normalizes Phase 2 page metrics, OCR, variable suggestions and page anomalies defensively', () => {
    const parsed = parseDocumentResult({
      ...backendResult,
      schema_version: '2.0',
      comparison_mode: 'template',
      total_pages: 3,
      reference_page_count: 3,
      candidate_page_count: 2,
      reference: { ...backendResult.reference, page_count: 3 },
      candidate: { ...backendResult.candidate, page_count: 2 },
      findings: [],
      pages: [
        {
          page_number: 1,
          page_status: 'matched',
          page_risk: 0.04,
          page_confidence: 0.96,
          page_coverage: 1,
          candidate_page_url: '/assets/page-1',
          reference_page_url: '/assets/reference-1',
          findings: [],
          ocr_summary: {
            reference_provider: 'pymupdf_embedded_text',
            candidate_provider: 'RapidOCR',
            reference_device: 'cpu',
            candidate_device: 'cpu',
            reference_confidence: 99,
            candidate_confidence: 91,
            reference_characters: 150,
            candidate_characters: 144,
            reference_succeeded: true,
            candidate_succeeded: true,
          },
          suggested_variable_regions: [
            {
              id: 'field-name',
              page: 1,
              region_role: 'variable',
              confidence: 0.89,
              reason: 'Value follows the Name label.',
              bbox: [0.2, 0.3, 0.4, 0.08],
            },
          ],
        },
        {
          page_number: 2,
          status: 'page_reordered',
          risk_score: 82,
          candidate_image_url: '/assets/page-2',
          reference_image_url: '/assets/reference-3',
          findings: [],
          reference_page_number: 3,
          candidate_page_number: 2,
        },
        {
          page_number: 3,
          status: 'missing_page',
          risk_score: 70,
          candidate_image_url: '',
          reference_image_url: '/assets/reference-2',
          findings: [],
          reference_page_number: 2,
          candidate_page_number: null,
        },
      ],
      page_order_anomalies: [
        {
          anomaly_type: 'page_reordered',
          title: 'Reordered page',
          page_number: 2,
          reference_page: 3,
          candidate_page: 2,
        },
        {
          anomaly_type: 'page_missing',
          title: 'Missing page',
          reference_page_number: 2,
          candidate_page_number: null,
        },
      ],
      aggregate: { pages_requiring_review: 2, max_page_risk: 82 },
    })

    expect(parsed.comparison_mode).toBe('template')
    expect(parsed.total_page_count).toBe(3)
    expect(parsed.candidate_page_count).toBe(2)
    expect(parsed.pages[0]).toMatchObject({ risk_score: 4, confidence_score: 96, coverage_score: 100 })
    expect(parsed.pages[0].ocr).toMatchObject({
      provider: 'RapidOCR',
      device: 'cpu',
      confidence_score: 91,
      succeeded: true,
      reference_provider: 'pymupdf_embedded_text',
    })
    expect(parsed.region_suggestions?.[0]).toMatchObject({ suggestion_id: 'field-name', role: 'variable' })
    expect(parsed.pages[1].status).toBe('reordered')
    expect(parsed.pages[2]).toMatchObject({ status: 'missing', candidate_page_number: null })
    expect(parsed.page_order_anomalies?.[0].type).toBe('reordered')
    expect(parsed.page_order_anomalies).toHaveLength(2)
    expect(parsed.page_order_anomalies?.[1]).toMatchObject({
      type: 'missing',
      page_number: 3,
      reference_page_number: 2,
      candidate_page_number: null,
    })
    expect(parsed.document_aggregate).toMatchObject({ reviewed_page_count: 2, highest_page_risk: 82 })

    const progress = apiInternals.parseProgressEvent({
      id: 'progress-2',
      current_page: 2,
      total_page_count: 3,
      current_page_stage: 'Raster OCR',
      text_provider: 'RapidOCR',
      execution_device: 'cpu',
      region: [0.4, 0.5, 0.2, 0.1],
      progress: 58,
    }, 'job-phase-2')
    expect(progress).toMatchObject({
      page_number: 2,
      total_pages: 3,
      page_stage: 'Raster OCR',
      ocr_provider: 'RapidOCR',
      ocr_device: 'cpu',
      localized_region: { x: 0.4, y: 0.5, width: 0.2, height: 0.1 },
    })
  })

  it('polls the final job after an SSE interruption and returns the completed result', async () => {
    const runningJob = {
      schema_version: '1.0',
      job_id: 'job-contract',
      state: 'running',
      progress: 56,
      current_stage: 'comparing_structure',
      current_stage_message: 'Comparing document structure',
      created_at: '2026-08-28T07:29:59Z',
      updated_at: '2026-08-28T07:30:00Z',
      result: null,
      error: null,
    }
    const completedJob = {
      ...runningJob,
      state: 'completed',
      progress: 100,
      current_stage: 'complete',
      current_stage_message: 'Analysis complete',
      result: backendResult,
    }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(runningJob))
      .mockResolvedValueOnce(jsonResponse(completedJob))
    vi.stubGlobal('fetch', fetchMock)

    const created: AnalysisJobCreated = {
      job_id: 'job-contract',
      state: 'queued',
      status_url: '/api/v1/analyses/job-contract',
      event_stream_url: '/api/v1/analyses/job-contract/events',
    }
    const handlers: AnalysisWatchHandlers = {
      onConnection: vi.fn(),
      onProgress: vi.fn(),
      onComplete: vi.fn(),
      onError: vi.fn(),
    }
    const stop = watchAnalysis(created, handlers)

    await vi.waitFor(() => expect(handlers.onProgress).toHaveBeenCalledWith(
      expect.objectContaining({ stage_id: 'comparing_structure', progress: 56 }),
    ))
    FakeEventSource.instances[0].onerror?.(new Event('error'))

    await vi.waitFor(() => expect(handlers.onComplete).toHaveBeenCalledOnce())
    expect(handlers.onComplete).toHaveBeenCalledWith(
      expect.objectContaining({ overall_tampering_risk: 81, finding_count: 1 }),
    )
    expect(handlers.onConnection).toHaveBeenLastCalledWith('closed')
    expect(FakeEventSource.instances[0].closed).toBe(true)
    expect(handlers.onError).not.toHaveBeenCalled()
    stop()
  })
})
