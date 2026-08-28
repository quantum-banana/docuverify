import type { HTMLAttributes, ReactNode } from 'react'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type {
  AnalysisJobCreated,
  AnalysisWatchHandlers,
  DocumentResult,
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

let latestHandlers: AnalysisWatchHandlers | undefined

const beginDemo = async () => {
  const user = userEvent.setup()
  render(<App />)
  await user.click(screen.getByRole('button', { name: /run synthetic demo/i }))
  await waitFor(() => expect(apiMocks.watchAnalysis).toHaveBeenCalledOnce())
  if (!latestHandlers) throw new Error('Analysis handlers were not registered')
  return { user, handlers: latestHandlers }
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
