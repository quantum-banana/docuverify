import type { HTMLAttributes, ReactNode } from 'react'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type {
  AnalysisJobCreated,
  AnalysisWatchHandlers,
  DocumentResult,
  Finding,
  ProgressEvent,
} from '../types/contracts'

const apiMocks = vi.hoisted(() => ({
  createAnalysis: vi.fn(),
  runDemo: vi.fn(),
  watchAnalysis: vi.fn(),
}))

vi.mock('../api/client', () => apiMocks)

vi.mock('framer-motion', async () => {
  const react = await import('react')
  const makeMotion = (tag: string) =>
    react.forwardRef<HTMLElement, HTMLAttributes<HTMLElement> & Record<string, unknown>>((props, ref) => {
      const cleanProps = { ...props }
      for (const key of ['initial', 'animate', 'exit', 'transition', 'layout', 'whileHover']) {
        delete cleanProps[key]
      }
      return react.createElement(tag, { ...cleanProps, ref }, props.children as ReactNode)
    })
  return {
    AnimatePresence: ({ children }: { children: ReactNode }) => children,
    motion: {
      main: makeMotion('main'),
      span: makeMotion('span'),
      button: makeMotion('button'),
      aside: makeMotion('aside'),
    },
    useReducedMotion: () => true,
  }
})

import App from '../App'

const createdJob: AnalysisJobCreated = {
  job_id: 'job-12345678',
  state: 'queued',
  status_url: '/api/v1/analyses/job-12345678',
  event_stream_url: '/api/v1/analyses/job-12345678/events',
}

const finding = {
  finding_id: 'finding-1',
  page_number: 1,
  category: 'text_change',
  title: 'Changed result field',
  explanation: 'The questioned result differs from the trusted reference in this region.',
  bounding_box: { x: 0.31, y: 0.42, width: 0.28, height: 0.08 },
  risk_score: 91,
  confidence_score: 96,
  severity: 'critical',
  evidence_source: 'Pixel difference, embedded text',
  candidate_crop_url: '/api/v1/analyses/job-12345678/assets/candidate-crop',
  reference_crop_url: '/api/v1/analyses/job-12345678/assets/reference-crop',
  difference_overlay_url: '/api/v1/analyses/job-12345678/assets/difference-overlay',
  measurements: {
    changed_pixel_ratio: 0.182,
    text_mismatch: true,
  },
}

const completedResult: DocumentResult = {
  schema_version: '1.0',
  job_id: createdJob.job_id,
  comparison_mode: 'exact',
  overall_tampering_risk: 84,
  risk_label: 'Critical tampering risk',
  assessment_confidence: 96,
  analysis_coverage: 100,
  alignment_quality: 94,
  finding_count: 1,
  processing_duration_ms: 1840,
  findings: [finding],
  pages: [
    {
      page_number: 1,
      width: 1200,
      height: 1697,
      candidate_image_url: '/api/v1/analyses/job-12345678/assets/candidate-page',
      reference_image_url: '/api/v1/analyses/job-12345678/assets/reference-page',
      findings: [finding],
    },
  ],
}

const pageOneFinding: Finding = {
  finding_id: 'finding-page-1',
  page_number: 1,
  category: 'layout_displacement',
  title: 'Header shifted',
  explanation: 'The page heading moved relative to the trusted template.',
  bounding_box: { x: 0.12, y: 0.08, width: 0.42, height: 0.07 },
  risk_score: 41,
  confidence_score: 89,
  severity: 'medium',
  evidence_source: 'Page alignment',
  candidate_crop_url: '/assets/page-1-candidate-crop',
  reference_crop_url: '/assets/page-1-reference-crop',
  difference_overlay_url: '/assets/page-1-difference',
  measurements: { displacement_px: 8 },
}

const pageTwoFinding: Finding = {
  finding_id: 'finding-page-2',
  page_number: 2,
  category: 'typography_inconsistency',
  title: 'Typography inconsistency',
  explanation: 'The variable value has a different baseline and character weight.',
  bounding_box: { x: 0.54, y: 0.48, width: 0.27, height: 0.09 },
  risk_score: 88,
  confidence_score: 94,
  severity: 'high',
  evidence_source: 'Raster OCR, typography geometry',
  candidate_crop_url: '/assets/page-2-candidate-crop',
  reference_crop_url: '/assets/page-2-reference-crop',
  difference_overlay_url: '/assets/page-2-difference',
  measurements: { baseline_shift_px: 6, background_edge_score: 0.72 },
}

const multiPageResult: DocumentResult = {
  schema_version: '2.0',
  job_id: createdJob.job_id,
  comparison_mode: 'template',
  overall_tampering_risk: 78,
  risk_label: 'Critical tampering risk',
  assessment_confidence: 91,
  analysis_coverage: 92,
  alignment_quality: 87,
  finding_count: 2,
  processing_duration_ms: 6240,
  total_page_count: 3,
  reference_page_count: 3,
  candidate_page_count: 3,
  findings: [pageOneFinding, pageTwoFinding],
  pages: [
    {
      page_number: 1,
      width: 1200,
      height: 1697,
      candidate_image_url: '/assets/candidate-page-1',
      reference_image_url: '/assets/reference-page-1',
      findings: [pageOneFinding],
      status: 'matched',
      risk_score: 41,
      confidence_score: 92,
      coverage_score: 100,
      finding_count: 1,
    },
    {
      page_number: 2,
      width: 1200,
      height: 1697,
      candidate_image_url: '/assets/candidate-page-2',
      reference_image_url: '/assets/reference-page-2',
      findings: [pageTwoFinding],
      status: 'reordered',
      risk_score: 88,
      confidence_score: 94,
      coverage_score: 96,
      finding_count: 1,
      ocr: {
        source: 'raster_ocr',
        provider: 'RapidOCR',
        device: 'cpu',
        confidence_score: 93,
        status: 'completed',
        character_count: 144,
      },
      region_suggestions: [
        {
          suggestion_id: 'variable-name',
          page_number: 2,
          role: 'variable',
          confidence_score: 91,
          reason: 'Value follows a stable Name label.',
          label: 'Name',
          bounding_box: { x: 0.51, y: 0.39, width: 0.31, height: 0.07 },
        },
      ],
    },
    {
      page_number: 3,
      width: 1200,
      height: 1697,
      candidate_image_url: '',
      reference_image_url: '/assets/reference-page-3',
      findings: [],
      status: 'missing',
      risk_score: 72,
      confidence_score: 99,
      coverage_score: 100,
      finding_count: 0,
      candidate_page_number: null,
    },
  ],
  region_suggestions: [
    {
      suggestion_id: 'variable-name',
      page_number: 2,
      role: 'variable',
      confidence_score: 91,
      reason: 'Value follows a stable Name label.',
      label: 'Name',
      bounding_box: { x: 0.51, y: 0.39, width: 0.31, height: 0.07 },
    },
  ],
  page_order_anomalies: [
    {
      anomaly_id: 'reordered-2',
      type: 'reordered',
      title: 'Reordered page',
      explanation: 'Candidate page 2 most closely corresponds to reference page 3.',
      page_number: 2,
      reference_page_number: 3,
      candidate_page_number: 2,
      severity: 'medium',
    },
    {
      anomaly_id: 'missing-3',
      type: 'missing',
      title: 'Missing page',
      explanation: 'Reference page 3 has no candidate counterpart.',
      page_number: 3,
      reference_page_number: 3,
      candidate_page_number: null,
      severity: 'high',
    },
    {
      anomaly_id: 'added-4',
      type: 'added',
      title: 'Added page',
      explanation: 'The candidate contains a page absent from the trusted reference.',
      page_number: null,
      reference_page_number: null,
      candidate_page_number: 4,
      severity: 'high',
    },
  ],
  document_aggregate: {
    total_page_count: 3,
    matched_page_count: 1,
    reviewed_page_count: 3,
    clean_page_count: 0,
    anomaly_count: 3,
    finding_count: 2,
    highest_page_risk: 88,
  },
}

let latestHandlers: AnalysisWatchHandlers | undefined

const beginDemo = async () => {
  const user = userEvent.setup()
  render(<App />)
  await user.click(screen.getByRole('button', { name: /run synthetic demo/i }))
  await waitFor(() => expect(apiMocks.watchAnalysis).toHaveBeenCalledOnce())
  if (!latestHandlers) throw new Error('Analysis handlers were not registered')
  return { user, handlers: latestHandlers }
}

const completeMultiPageDemo = async () => {
  const context = await beginDemo()
  context.handlers.onComplete(multiPageResult)
  await screen.findByRole('heading', { name: 'Evidence review' })
  return context
}

describe('DocuVerify Phase 1 experience', () => {
  beforeEach(() => {
    latestHandlers = undefined
    apiMocks.createAnalysis.mockReset().mockResolvedValue(createdJob)
    apiMocks.runDemo.mockReset().mockResolvedValue(createdJob)
    apiMocks.watchAnalysis.mockReset().mockImplementation(
      (_created: AnalysisJobCreated, handlers: AnalysisWatchHandlers) => {
        latestHandlers = handlers
        handlers.onConnection('live')
        return vi.fn()
      },
    )
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('renders the initial upload state and keeps comparison disabled until both files exist', () => {
    render(<App />)

    expect(screen.getByText(/upload the document\./i)).toBeInTheDocument()
    expect(screen.getByText(/trust what can be explained/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /trusted reference: choose a file/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /questioned document: choose a file/i })).toBeInTheDocument()
    expect(screen.getByText('Exact comparison')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /start comparison/i })).toBeDisabled()
    expect(screen.getByText(/private by design/i)).toBeInTheDocument()
  })

  it('accepts both files and starts an exact-document upload comparison', async () => {
    const user = userEvent.setup()
    render(<App />)
    const reference = new File(['reference'], 'reference.pdf', { type: 'application/pdf' })
    const candidate = new File(['candidate'], 'candidate.png', { type: 'image/png' })

    await user.upload(screen.getByTestId('reference-input'), reference)
    await user.upload(screen.getByTestId('candidate-input'), candidate)
    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalledWith(candidate))
    const start = screen.getByRole('button', { name: /start comparison/i })
    expect(start).toBeEnabled()
    await user.click(start)

    await waitFor(() => expect(apiMocks.createAnalysis).toHaveBeenCalledWith(reference, candidate, 'exact'))
    expect(apiMocks.watchAnalysis).toHaveBeenCalledWith(createdJob, expect.any(Object))
    expect(screen.getByText(/live forensic analysis/i)).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /questioned document/i })).toHaveAttribute(
      'src',
      'blob:document-preview',
    )
  })

  it('selects template mode and reports safe multi-page and OCR upload capabilities', async () => {
    const user = userEvent.setup()
    render(<App />)
    const modeGroup = screen.getByRole('group', { name: /comparison mode/i })
    const exactMode = within(modeGroup).getByRole('radio', { name: /exact comparison/i })
    const templateMode = within(modeGroup).getByRole('radio', { name: /template comparison/i })

    expect(exactMode).toBeChecked()
    expect(templateMode).not.toBeChecked()
    await user.click(templateMode)
    expect(templateMode).toBeChecked()

    const reference = new File(['%PDF-1.7'], 'reference-multipage.pdf', { type: 'application/pdf' })
    const candidate = new File(['candidate'], 'candidate.png', { type: 'image/png' })
    await user.upload(screen.getByTestId('reference-input'), reference)
    await user.upload(screen.getByTestId('candidate-input'), candidate)

    const summary = screen.getByRole('region', { name: /multi-page upload summary/i })
    expect(within(summary).getByText('Pending validation')).toBeInTheDocument()
    expect(within(summary).getByText('1 page')).toBeInTheDocument()
    expect(within(summary).getByText(/10-page limit/i)).toBeInTheDocument()
    expect(within(summary).getByText(/raster ocr ready/i)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /start comparison/i }))
    await waitFor(() => expect(apiMocks.createAnalysis).toHaveBeenCalledWith(reference, candidate, 'template'))
  })

  it('never loads an uploaded PDF in the browser and switches to the backend-safe PNG', async () => {
    const user = userEvent.setup()
    const { container } = render(<App />)
    const reference = new File(['reference'], 'reference.png', { type: 'image/png' })
    const candidate = new File(['%PDF-1.7'], 'questioned.pdf', { type: 'application/pdf' })

    await user.upload(screen.getByTestId('reference-input'), reference)
    await user.upload(screen.getByTestId('candidate-input'), candidate)
    expect(URL.createObjectURL).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: /start comparison/i }))
    await waitFor(() => expect(apiMocks.watchAnalysis).toHaveBeenCalledOnce())
    expect(screen.getByTestId('document-placeholder')).toBeInTheDocument()
    expect(container.querySelector('object')).not.toBeInTheDocument()
    expect(container.innerHTML).not.toContain('blob:')
    if (!latestHandlers) throw new Error('Analysis handlers were not registered')

    latestHandlers.onProgress({
      event_id: 'evt-safe-preview',
      job_id: createdJob.job_id,
      stage_id: 'aligning_reference',
      message: 'Candidate preview ready',
      progress: 39,
      page_number: 1,
      total_pages: 1,
      timestamp: '2026-08-28T07:29:59Z',
      finding_count: 0,
      candidate_page_url: '/api/v1/analyses/job-12345678/assets/candidate-page',
    })

    expect(await screen.findByRole('img', { name: /questioned document/i })).toHaveAttribute(
      'src',
      '/api/v1/analyses/job-12345678/assets/candidate-page',
    )
    expect(container.querySelector('object')).not.toBeInTheDocument()
    expect(URL.createObjectURL).not.toHaveBeenCalled()
  })

  it('initiates the bundled demo and reflects a real progress event', async () => {
    const { handlers } = await beginDemo()
    expect(apiMocks.runDemo).toHaveBeenCalledOnce()

    handlers.onProgress({
      event_id: 'evt-4',
      job_id: createdJob.job_id,
      stage_id: 'aligning_reference',
      message: 'Candidate preview ready',
      progress: 39,
      page_number: 1,
      total_pages: 1,
      timestamp: '2026-08-28T07:29:59Z',
      finding_count: 0,
      candidate_page_url: '/api/v1/analyses/job-12345678/assets/candidate-page',
    })
    expect(await screen.findByRole('img', { name: /questioned document/i })).toHaveAttribute(
      'src',
      '/api/v1/analyses/job-12345678/assets/candidate-page',
    )

    const progress: ProgressEvent = {
      event_id: 'evt-7',
      job_id: createdJob.job_id,
      stage_id: 'localizing_differences',
      message: 'Localized two candidate regions',
      progress: 68,
      page_number: 1,
      total_pages: 1,
      timestamp: '2026-08-28T07:30:00Z',
      finding_count: 2,
    }
    handlers.onProgress(progress)

    expect(await screen.findByRole('heading', { name: /localizing suspicious differences/i })).toBeInTheDocument()
    expect(screen.getByText('Localized two candidate regions')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: /analysis progress/i })).toHaveAttribute('aria-valuenow', '68')
    expect(screen.getByText('02')).toBeInTheDocument()
    expect(screen.getByText(/live connection/i)).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /questioned document/i })).toHaveAttribute(
      'src',
      '/api/v1/analyses/job-12345678/assets/candidate-page',
    )
  })

  it('tracks the current page, OCR provider and thumbnails from page-aware progress', async () => {
    const { handlers } = await beginDemo()
    handlers.onProgress({
      event_id: 'page-1-preview',
      job_id: createdJob.job_id,
      stage_id: 'rendering_documents',
      message: 'Preparing page 1 of 3',
      progress: 18,
      page_number: 1,
      total_pages: 3,
      timestamp: '2026-08-28T07:30:01Z',
      finding_count: 0,
      candidate_page_url: '/assets/progress-page-1',
    })
    handlers.onProgress({
      event_id: 'page-2-ocr',
      job_id: createdJob.job_id,
      stage_id: 'extracting_text',
      page_stage: 'Raster OCR on page 2',
      message: 'Extracting text from page 2',
      progress: 54,
      page_number: 2,
      total_pages: 3,
      timestamp: '2026-08-28T07:30:02Z',
      finding_count: 1,
      candidate_page_url: '/assets/progress-page-2',
      ocr_provider: 'RapidOCR',
      ocr_device: 'cpu',
    })

    expect(await screen.findByText(/live forensic analysis · page 2 of 3/i)).toBeInTheDocument()
    expect(screen.getAllByText('Raster OCR on page 2')).toHaveLength(2)
    expect(screen.getByText('RapidOCR')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: /analysis progress/i })).toHaveAttribute('aria-valuenow', '54')
    const pageProgress = screen.getByRole('region', { name: /analysis page progress/i })
    expect(within(pageProgress).getByRole('img', { name: /page 1 analysis thumbnail/i })).toHaveAttribute('src', '/assets/progress-page-1')
    expect(within(pageProgress).getByRole('img', { name: /page 2 analysis thumbnail/i })).toHaveAttribute('src', '/assets/progress-page-2')
    expect(screen.getByRole('img', { name: /questioned document/i })).toHaveAttribute('src', '/assets/progress-page-2')
  })

  it('renders completed risk metrics and opens evidence details from the normalized SVG marker', async () => {
    const { user, handlers } = await beginDemo()
    handlers.onComplete(completedResult)

    expect(await screen.findByRole('heading', { name: 'Evidence review' })).toBeInTheDocument()
    expect(screen.getByText('Critical tampering risk')).toBeInTheDocument()
    expect(screen.getByText('Assessment confidence')).toBeInTheDocument()
    expect(screen.getByText('Analysis coverage')).toBeInTheDocument()
    expect(screen.getByText('1.8 s')).toBeInTheDocument()

    const overlay = screen.getByLabelText('1 evidence marker')
    const marker = within(overlay).getByRole('button', { name: /open evidence 1: changed result field/i })
    await user.click(marker)

    const drawer = await screen.findByRole('dialog', { name: /changed result field/i })
    expect(within(drawer).getByText(/questioned result differs/i)).toBeInTheDocument()
    expect(within(drawer).getByText('Questioned')).toBeInTheDocument()
    expect(within(drawer).getByText('Trusted reference')).toBeInTheDocument()
    expect(within(drawer).getByText('Difference overlay')).toBeInTheDocument()
    expect(within(drawer).getByText('Changed Pixel Ratio')).toBeInTheDocument()
    expect(within(drawer).getByText(/x 0\.310 · y 0\.420/i)).toBeInTheDocument()
  })

  it('renders a risk filmstrip and keeps markers scoped to the repeatedly selected page', async () => {
    const { user } = await completeMultiPageDemo()
    const filmstrip = screen.getByRole('region', { name: /document pages/i })
    expect(within(filmstrip).getAllByRole('button')).toHaveLength(5)
    expect(screen.getByRole('img', { name: /questioned document · page 1/i })).toHaveAttribute('src', '/assets/candidate-page-1')
    expect(within(screen.getByLabelText('1 evidence marker')).getByRole('button', { name: /header shifted/i })).toBeInTheDocument()

    await user.click(within(filmstrip).getByRole('button', { name: /page 2: 88 risk, 1 finding, reordered page/i }))
    expect(screen.getByRole('img', { name: /questioned document · page 2/i })).toHaveAttribute('src', '/assets/candidate-page-2')
    const pageTwoOverlay = screen.getByLabelText('1 evidence marker')
    expect(within(pageTwoOverlay).getByRole('button', { name: /typography inconsistency/i })).toBeInTheDocument()
    expect(within(pageTwoOverlay).queryByRole('button', { name: /header shifted/i })).not.toBeInTheDocument()
    expect(screen.getByLabelText('1 suggested variable region')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /next page/i }))
    expect(screen.getByText(/candidate page missing/i)).toBeInTheDocument()
    expect(screen.queryByLabelText(/evidence marker/i)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /previous page/i }))
    expect(screen.getByRole('img', { name: /questioned document · page 2/i })).toHaveAttribute('src', '/assets/candidate-page-2')
    expect(within(screen.getByLabelText('1 evidence marker')).getByRole('button', { name: /typography inconsistency/i })).toBeInTheDocument()
  })

  it('navigates a document finding to its page and preserves correct evidence after page switches', async () => {
    const { user } = await completeMultiPageDemo()
    await user.click(screen.getByRole('button', { name: /open finding on page 2: typography inconsistency/i }))

    let drawer = await screen.findByRole('dialog', { name: /typography inconsistency/i })
    expect(within(drawer).getByText('Page 2')).toBeInTheDocument()
    expect(within(drawer).getByRole('img', { name: /questioned for selected finding/i })).toHaveAttribute('src', '/assets/page-2-candidate-crop')
    expect(screen.getByRole('img', { name: /questioned document · page 2/i })).toHaveAttribute('src', '/assets/candidate-page-2')
    await user.click(within(drawer).getByRole('button', { name: /close evidence drawer/i }))

    const filmstrip = screen.getByRole('region', { name: /document pages/i })
    await user.click(within(filmstrip).getByRole('button', { name: /page 1: 41 risk/i }))
    await user.click(within(filmstrip).getByRole('button', { name: /page 2: 88 risk/i }))
    await user.click(within(screen.getByLabelText('1 evidence marker')).getByRole('button', { name: /typography inconsistency/i }))
    drawer = await screen.findByRole('dialog', { name: /typography inconsistency/i })
    expect(within(drawer).getByText('Page 2')).toBeInTheDocument()
    expect(within(drawer).getByText(/different baseline and character weight/i)).toBeInTheDocument()
  })

  it('surfaces mode, aggregate, suggested-region and missing, added and reordered-page indicators', async () => {
    await completeMultiPageDemo()
    expect(screen.getAllByText('Template comparison').length).toBeGreaterThan(0)
    const aggregate = screen.getByRole('region', { name: /document aggregate/i })
    expect(within(aggregate).getByText('Reference')).toBeInTheDocument()
    expect(within(aggregate).getAllByText('3 pages')).toHaveLength(2)
    expect(within(aggregate).getByText('Variable regions')).toBeInTheDocument()

    const anomalies = screen.getByRole('region', { name: /page anomalies/i })
    expect(within(anomalies).getByText('Missing page')).toBeInTheDocument()
    expect(within(anomalies).getByText('Added page')).toBeInTheDocument()
    expect(within(anomalies).getByText('Reordered page')).toBeInTheDocument()
  })

  it('shows a structured failure state and a route back to upload', async () => {
    const { user, handlers } = await beginDemo()
    handlers.onError({ code: 'render_failed', message: 'The questioned PDF could not be rendered.' })

    const alert = await screen.findByRole('alert')
    expect(within(alert).getByText(/questioned pdf could not be rendered/i)).toBeInTheDocument()
    expect(within(alert).getByText('render_failed')).toBeInTheDocument()
    await user.click(within(alert).getByRole('button', { name: /return to upload/i }))
    expect(screen.getByRole('heading', { name: /compare two documents/i })).toBeInTheDocument()
  })
})
