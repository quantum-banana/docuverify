import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { parseDocumentResult, runDemo, watchAnalysis } from '../api/client'
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
