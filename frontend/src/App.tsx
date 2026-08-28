import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
} from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { createAnalysis, runDemo, watchAnalysis } from './api/client'
import { DocumentViewer } from './components/DocumentViewer'
import { EvidenceDrawer } from './components/EvidenceDrawer'
import { FileDropzone } from './components/FileDropzone'
import {
  AlertIcon,
  ArrowIcon,
  CheckIcon,
  ChevronIcon,
  FileIcon,
  LockIcon,
  RefreshIcon,
  ScanIcon,
  ShieldIcon,
  SignalIcon,
  SparkIcon,
} from './components/Icons'
import type {
  AnalysisError,
  AnalysisJobCreated,
  ComparisonMode,
  ConnectionState,
  DocumentResult,
  Finding,
  PageOrderAnomaly,
  PageResult,
  ProgressEvent,
  StageId,
} from './types/contracts'

type Screen = 'upload' | 'analysis' | 'complete' | 'error'
type PagePreviewMap = Record<number, string>

const stages: Array<{ id: StageId; label: string }> = [
  { id: 'validating_uploads', label: 'Validate files' },
  { id: 'rendering_documents', label: 'Render page' },
  { id: 'normalizing_pages', label: 'Normalize' },
  { id: 'extracting_text', label: 'Extract / OCR' },
  { id: 'aligning_reference', label: 'Align reference' },
  { id: 'identifying_regions', label: 'Map field roles' },
  { id: 'comparing_structure', label: 'Compare structure' },
  { id: 'comparing_typography', label: 'Inspect typography' },
  { id: 'localizing_differences', label: 'Localize evidence' },
  { id: 'scoring_evidence', label: 'Score page' },
  { id: 'aggregating_document', label: 'Aggregate document' },
  { id: 'preparing_result', label: 'Prepare result' },
]

const stageCopy: Record<string, { title: string; description: string }> = {
  queued: {
    title: 'Preparing the documents',
    description: 'The analysis is queued and will begin locally in a moment.',
  },
  validating_uploads: {
    title: 'Validating both documents',
    description: 'Checking file integrity, content type and the ten-page processing boundary.',
  },
  rendering_documents: {
    title: 'Rendering document pages',
    description: 'Creating browser-safe page images for precise visual inspection.',
  },
  normalizing_pages: {
    title: 'Normalizing the current page',
    description: 'Matching scale, orientation and colour space without hiding evidence.',
  },
  extracting_text: {
    title: 'Extracting text and labels',
    description: 'Using embedded text when reliable and raster OCR when a page is image-only.',
  },
  aligning_reference: {
    title: 'Aligning with the trusted reference',
    description: 'Registering corresponding pages so matching content occupies the same coordinates.',
  },
  identifying_regions: {
    title: 'Identifying fixed and variable fields',
    description: 'Separating stable labels from values that may legitimately vary in template mode.',
  },
  comparing_structure: {
    title: 'Comparing document structure',
    description: 'Inspecting layout, edges and pixels for meaningful changes.',
  },
  comparing_typography: {
    title: 'Comparing typography and compositing',
    description: 'Checking character appearance, baselines, spacing and background integrity.',
  },
  localizing_differences: {
    title: 'Localizing suspicious differences',
    description: 'Grouping visual and text signals into explainable candidate regions.',
  },
  scoring_evidence: {
    title: 'Scoring page evidence',
    description: 'Keeping tampering risk, confidence and analysis coverage distinct.',
  },
  aggregating_document: {
    title: 'Aggregating document risk',
    description: 'Combining page evidence without allowing clean pages to erase a strong finding.',
  },
  preparing_result: {
    title: 'Preparing the result',
    description: 'Generating evidence crops, overlays and normalized page markers.',
  },
  complete: {
    title: 'Analysis complete',
    description: 'The page-aware evidence package is ready for review.',
  },
}

const initialProgress: ProgressEvent = {
  event_id: '',
  job_id: '',
  stage_id: 'queued',
  message: 'Preparing the documents',
  progress: 0,
  page_number: 1,
  total_pages: 1,
  timestamp: '',
  finding_count: 0,
}

const modeDescription: Record<ComparisonMode, { title: string; short: string; detail: string }> = {
  exact: {
    title: 'Exact comparison',
    short: 'Exact document mode',
    detail: 'Every page, field and visual region is expected to match closely.',
  },
  template: {
    title: 'Template comparison',
    short: 'Template-aware mode',
    detail: 'Variable values may change; fixed labels, typography and compositing are still inspected.',
  },
}

const errorFromUnknown = (value: unknown): AnalysisError => {
  if (typeof value === 'object' && value && 'message' in value) {
    return {
      code: 'code' in value ? String((value as { code: unknown }).code) : 'request_error',
      message: String((value as { message: unknown }).message),
      field: 'field' in value ? String((value as { field: unknown }).field) : undefined,
    }
  }
  return { code: 'request_error', message: 'The local analysis service could not be reached.' }
}

const formatDuration = (milliseconds: number): string => {
  if (milliseconds < 1000) return `${Math.round(milliseconds)} ms`
  return `${(milliseconds / 1000).toFixed(milliseconds < 10_000 ? 1 : 0)} s`
}

const riskTone = (risk: number): 'low' | 'moderate' | 'high' | 'critical' => {
  if (risk >= 75) return 'critical'
  if (risk >= 50) return 'high'
  if (risk >= 25) return 'moderate'
  return 'low'
}

const currentStageIndex = (stageId: StageId): number => stages.findIndex((stage) => stage.id === stageId)

const pageRisk = (page: PageResult): number =>
  page.risk_score ?? page.findings.reduce((highest, finding) => Math.max(highest, finding.risk_score), 0)

const pageFindingCount = (page: PageResult): number => page.finding_count ?? page.findings.length

const statusTitle = (status: string): string => {
  const normalized = status.toLowerCase().replaceAll('-', '_')
  const copy: Record<string, string> = {
    matched: 'Matched',
    completed: 'Matched',
    processing: 'Processing',
    missing: 'Missing page',
    added: 'Added page',
    reordered: 'Reordered page',
    dimension_mismatch: 'Dimension mismatch',
    error: 'Page error',
  }
  return copy[normalized] ?? normalized.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

const isPdf = (file: File): boolean =>
  file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')

const uploadPageSummary = (file: File | null): { value: string; detail: string } => {
  if (!file) return { value: 'Not selected', detail: 'Choose a PDF or image' }
  if (isPdf(file)) return { value: 'Pending validation', detail: 'Local engine reports 1–10 pages' }
  return { value: '1 page', detail: 'Raster image document' }
}

function AppHeader({ quiet = false }: { quiet?: boolean }) {
  return (
    <header className={`site-header${quiet ? ' site-header--quiet' : ''}`}>
      <button className="brand" type="button" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}>
        <span className="brand__mark"><ShieldIcon /></span>
        <span>DOCU<span>VERIFY</span></span>
      </button>
      <div className="header-context">
        <span className="header-context__line" />
        <span>Phase 02</span>
        <span className="local-badge"><span /> Local analysis</span>
      </div>
    </header>
  )
}

function UploadScreen({
  reference,
  candidate,
  comparisonMode,
  onReference,
  onCandidate,
  onMode,
  onStart,
  onDemo,
  submitting,
}: {
  reference: File | null
  candidate: File | null
  comparisonMode: ComparisonMode
  onReference: (file: File | null) => void
  onCandidate: (file: File | null) => void
  onMode: (mode: ComparisonMode) => void
  onStart: () => void
  onDemo: () => void
  submitting: boolean
}) {
  const reduceMotion = useReducedMotion()
  const ready = Boolean(reference && candidate)
  const referenceSummary = uploadPageSummary(reference)
  const candidateSummary = uploadPageSummary(candidate)

  return (
    <motion.main
      className="landing"
      initial={reduceMotion ? false : { opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: reduceMotion ? 0 : 0.45 }}
    >
      <section className="landing-hero">
        <div className="landing-hero__copy">
          <div className="eyebrow-row">
            <span className="eyebrow"><span className="eyebrow__pulse" /> Explainable document forensics</span>
            <span className="eyebrow-number">02 / MULTI-PAGE</span>
          </div>
          <h1>
            Upload the document.<br />
            See the <em>evidence.</em><br />
            Trust what can be explained.
          </h1>
          <p>
            Compare up to ten pages against a trusted reference. DocuVerify follows each page,
            reads raster documents locally and keeps every finding tied to its evidence.
          </p>
          <div className="hero-proof">
            <span><CheckIcon /> Page-aware comparison</span>
            <span><CheckIcon /> Local raster OCR</span>
            <span><CheckIcon /> No cloud upload</span>
          </div>
        </div>

        <div className="landing-hero__motif" aria-hidden="true">
          <div className="motif-document motif-document--back" />
          <div className="motif-document">
            <span className="motif-document__crest"><ShieldIcon /></span>
            <span className="motif-document__line motif-document__line--short" />
            <span className="motif-document__line" />
            <span className="motif-document__line" />
            <span className="motif-document__target" />
            <span className="motif-document__scan" />
          </div>
          <span className="motif-label motif-label--one">ALIGN</span>
          <span className="motif-label motif-label--two">INSPECT</span>
          <span className="motif-coordinate motif-coordinate--one">PAGE 02 / 03</span>
          <span className="motif-coordinate motif-coordinate--two">OCR / LOCAL</span>
        </div>
      </section>

      <section className="upload-workbench" aria-labelledby="upload-title">
        <div className="workbench-header">
          <div>
            <span className="eyebrow">Trusted-reference verification</span>
            <h2 id="upload-title">Compare two documents</h2>
          </div>
          <div className="mode-state"><span /> {modeDescription[comparisonMode].short}</div>
        </div>

        <div className="dropzone-grid">
          <FileDropzone
            id="reference"
            eyebrow="Trusted reference"
            title="Choose the known-good document"
            description="Drop the document you trust here, or browse"
            file={reference}
            onFile={onReference}
            tone="reference"
          />
          <div className="compare-bridge" aria-hidden="true"><span>VS</span></div>
          <FileDropzone
            id="candidate"
            eyebrow="Questioned document"
            title="Choose the document to inspect"
            description="Drop the candidate document here, or browse"
            file={candidate}
            onFile={onCandidate}
            tone="candidate"
          />
        </div>

        <section className="upload-capabilities" aria-label="Multi-page upload summary">
          <div className="upload-summary-card">
            <span>Reference pages</span>
            <strong>{referenceSummary.value}</strong>
            <small>{referenceSummary.detail}</small>
          </div>
          <div className="upload-summary-card">
            <span>Candidate pages</span>
            <strong>{candidateSummary.value}</strong>
            <small>{candidateSummary.detail}</small>
          </div>
          <div className="upload-summary-card upload-summary-card--limit">
            <FileIcon />
            <div><strong>10-page limit</strong><small>PDFs process sequentially to bound memory</small></div>
          </div>
          <div className="upload-summary-card upload-summary-card--ocr">
            <ScanIcon />
            <div><strong>Raster OCR ready</strong><small>Embedded text preferred · CPU OCR fallback</small></div>
          </div>
        </section>

        <div className="comparison-row">
          <fieldset className="mode-selector" aria-label="Comparison mode">
            <legend className="visually-hidden">Choose a comparison mode</legend>
            {(['exact', 'template'] as const).map((mode) => (
              <label key={mode} className={`mode-option${comparisonMode === mode ? ' is-selected' : ''}`}>
                <input
                  type="radio"
                  name="mode"
                  value={mode}
                  checked={comparisonMode === mode}
                  onChange={() => onMode(mode)}
                />
                <span className="mode-option__radio"><span /></span>
                <span>
                  <strong>{modeDescription[mode].title}</strong>
                  <small>{modeDescription[mode].detail}</small>
                </span>
                <span className="mode-option__tag">{comparisonMode === mode ? 'ACTIVE' : 'AVAILABLE'}</span>
              </label>
            ))}
          </fieldset>
          <div className="action-group">
            <button className="button button--ghost" type="button" onClick={onDemo} disabled={submitting}>
              <SparkIcon /> {submitting ? 'Starting…' : 'Run synthetic demo'}
            </button>
            <button className="button button--primary" type="button" onClick={onStart} disabled={!ready || submitting}>
              {submitting ? 'Preparing…' : 'Start comparison'} <ArrowIcon />
            </button>
          </div>
        </div>

        <div className="privacy-note">
          <LockIcon />
          <div>
            <strong>Private by design</strong>
            <span>Documents are processed by the local backend and retained only for the configured cleanup window.</span>
          </div>
          <span className="privacy-note__stamp">LOCALHOST / TLS NOT REQUIRED</span>
        </div>
      </section>
    </motion.main>
  )
}

function ConnectionBadge({ state }: { state: ConnectionState }) {
  const copy: Record<ConnectionState, string> = {
    connecting: 'Connecting',
    live: 'Live connection',
    reconnecting: 'Reconnecting',
    polling: 'Status fallback',
    closed: 'Stream closed',
  }
  return (
    <span className={`connection-badge connection-badge--${state}`}>
      <SignalIcon /> <span>{copy[state]}</span>
    </span>
  )
}

function AnalysisPageStrip({
  currentPage,
  totalPages,
  pagePreviews,
}: {
  currentPage: number
  totalPages: number
  pagePreviews: PagePreviewMap
}) {
  return (
    <section className="analysis-page-strip" aria-label="Analysis page progress">
      <div className="panel-label"><span>Document pages</span><small>SEQUENTIAL</small></div>
      <div className="analysis-page-strip__list" role="list">
        {Array.from({ length: Math.min(10, Math.max(1, totalPages)) }, (_, index) => index + 1).map((pageNumber) => {
          const preview = pagePreviews[pageNumber]
          const state = pageNumber < currentPage ? 'complete' : pageNumber === currentPage ? 'current' : 'pending'
          return (
            <div className={`analysis-page-thumb is-${state}`} role="listitem" key={pageNumber}>
              <div className="analysis-page-thumb__media">
                {preview ? (
                  <img src={preview} alt={`Page ${pageNumber} analysis thumbnail`} />
                ) : (
                  <span>{String(pageNumber).padStart(2, '0')}</span>
                )}
              </div>
              <div>
                <strong>Page {pageNumber}</strong>
                <small>{state}</small>
              </div>
              {state === 'complete' && <CheckIcon />}
              {state === 'current' && <span className="analysis-page-thumb__pulse" />}
            </div>
          )
        })}
      </div>
    </section>
  )
}

function AnalysisScreen({
  progress,
  connection,
  candidatePreview,
  pagePreviews,
  jobId,
}: {
  progress: ProgressEvent
  connection: ConnectionState
  candidatePreview?: string
  pagePreviews: PagePreviewMap
  jobId: string
}) {
  const reduceMotion = useReducedMotion()
  const copy = stageCopy[progress.stage_id] ?? {
    title: progress.message || 'Analysing document',
    description: 'The local forensic pipeline is processing the current page stage.',
  }
  const activeIndex = currentStageIndex(progress.stage_id)
  const currentPage = Math.min(progress.total_pages, Math.max(1, progress.page_number))
  const currentPreview = progress.candidate_page_url || pagePreviews[currentPage] ||
    (currentPage === 1 ? candidatePreview : undefined)
  const visiblePreviews = candidatePreview && !pagePreviews[1]
    ? { ...pagePreviews, 1: candidatePreview }
    : pagePreviews
  const pageStage = progress.page_stage || copy.title

  return (
    <motion.main
      className="analysis-layout"
      initial={reduceMotion ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: reduceMotion ? 0 : 0.35 }}
    >
      <div className="analysis-titlebar">
        <div>
          <span className="eyebrow">Live forensic analysis · Page {currentPage} of {progress.total_pages}</span>
          <h1>{copy.title}</h1>
          <p>{progress.message || copy.description}</p>
        </div>
        <div className="analysis-titlebar__status">
          <ConnectionBadge state={connection} />
          <span className="job-reference">JOB {jobId ? jobId.slice(0, 8).toUpperCase() : 'PENDING'}</span>
        </div>
      </div>

      <AnalysisPageStrip
        currentPage={currentPage}
        totalPages={progress.total_pages}
        pagePreviews={visiblePreviews}
      />

      <div className="analysis-grid">
        <DocumentViewer
          imageUrl={currentPreview}
          scanning
          progress={progress.progress}
          pageNumber={currentPage}
          totalPages={progress.total_pages}
          pageStatus="processing"
          localizedRegion={progress.localized_region}
        />

        <aside className="analysis-sidebar">
          <section className="progress-panel">
            <div className="progress-panel__top">
              <span>Document progress</span>
              <strong>{Math.round(progress.progress)}<small>%</small></strong>
            </div>
            <div
              className="progress-track"
              role="progressbar"
              aria-label="Analysis progress"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(progress.progress)}
            >
              <motion.span
                initial={false}
                animate={{ width: `${progress.progress}%` }}
                transition={{ duration: reduceMotion ? 0 : 0.35 }}
              />
            </div>
            <div className="page-progress-facts">
              <span><small>Current page</small><strong>{currentPage} / {progress.total_pages}</strong></span>
              <span><small>Page stage</small><strong>{pageStage}</strong></span>
              <span><small>Text engine</small><strong>{progress.ocr_provider || 'Detecting source'}</strong></span>
            </div>
            <div className="current-stage-card">
              <span className="current-stage-card__icon"><ScanIcon /></span>
              <div>
                <span>Current stage</span>
                <strong>{pageStage}</strong>
                <p>{copy.description}</p>
              </div>
            </div>
          </section>

          <section className="pipeline-panel">
            <div className="panel-label"><span>Forensic pipeline</span><small>PAGE {String(currentPage).padStart(2, '0')}</small></div>
            <ol className="pipeline-list">
              {stages.map((stage, index) => {
                const isComplete = activeIndex > index || progress.stage_id === 'complete'
                const isActive = activeIndex === index
                return (
                  <li key={stage.id} className={`${isComplete ? 'is-complete' : ''}${isActive ? ' is-active' : ''}`}>
                    <span className="pipeline-list__marker">{isComplete ? <CheckIcon /> : String(index + 1).padStart(2, '0')}</span>
                    <span>{stage.label}</span>
                    {isActive && <span className="pipeline-list__activity"><i /><i /><i /></span>}
                  </li>
                )
              })}
            </ol>
          </section>

          <section className="evidence-counter">
            <span>Evidence localized</span>
            <strong>{String(progress.finding_count).padStart(2, '0')}</strong>
            <small>Finding count updates from real backend page events.</small>
          </section>
        </aside>
      </div>
    </motion.main>
  )
}

function Metric({
  label,
  value,
  suffix = '%',
  displayValue,
}: {
  label: string
  value: number
  suffix?: string
  displayValue?: string
}) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{displayValue ?? Math.round(value)}{!displayValue && <small>{suffix}</small>}</strong>
      {!displayValue && <span className="metric__track"><i style={{ width: `${Math.max(0, Math.min(100, value))}%` }} /></span>}
    </div>
  )
}

function PageAnomalySummary({ anomalies }: { anomalies: PageOrderAnomaly[] }) {
  if (!anomalies.length) return null
  return (
    <section className="page-anomalies" aria-label="Page anomalies">
      <div className="page-anomalies__heading">
        <AlertIcon />
        <div><span>Page sequence review</span><strong>{anomalies.length} {anomalies.length === 1 ? 'anomaly' : 'anomalies'}</strong></div>
      </div>
      <div className="page-anomalies__list">
        {anomalies.map((anomaly) => (
          <article key={anomaly.anomaly_id} className={`page-anomaly page-anomaly--${riskTone(anomaly.severity === 'critical' ? 100 : anomaly.severity === 'high' ? 70 : 35)}`}>
            <span>{anomaly.title}</span>
            <p>{anomaly.explanation}</p>
            <small>
              Reference {anomaly.reference_page_number ?? '—'} · Candidate {anomaly.candidate_page_number ?? '—'}
            </small>
          </article>
        ))}
      </div>
    </section>
  )
}

function ResultScreen({
  result,
  selectedFinding,
  onSelectFinding,
  onClearFinding,
  onReset,
  onDemoAgain,
}: {
  result: DocumentResult
  selectedFinding: Finding | null
  onSelectFinding: (finding: Finding) => void
  onClearFinding: () => void
  onReset: () => void
  onDemoAgain: () => void
}) {
  const reduceMotion = useReducedMotion()
  const tone = riskTone(result.overall_tampering_risk)
  const pages = [...result.pages].sort((left, right) => left.page_number - right.page_number)
  const firstViewablePage = pages.find((page) => page.candidate_image_url) ?? pages[0]
  const [selectedPageNumber, setSelectedPageNumber] = useState(firstViewablePage?.page_number ?? 1)

  useEffect(() => {
    setSelectedPageNumber(firstViewablePage?.page_number ?? 1)
  }, [firstViewablePage?.page_number, result.job_id])

  const selectedPage = pages.find((page) => page.page_number === selectedPageNumber)
  const selectedPageIndex = pages.findIndex((page) => page.page_number === selectedPageNumber)
  const allFindings = result.findings.length ? result.findings : pages.flatMap((page) => page.findings)
  const currentPageFindings = selectedPage?.findings.length
    ? selectedPage.findings
    : allFindings.filter((finding) => finding.page_number === selectedPage?.page_number)
  const suggestions = (result.region_suggestions?.length
    ? result.region_suggestions
    : pages.flatMap((page) => page.region_suggestions ?? []))
    .filter((suggestion) => suggestion.page_number === selectedPage?.page_number)
  const anomalies = result.page_order_anomalies ?? []
  const totalPages = result.total_page_count ?? pages.length
  const referencePages = result.reference_page_count ?? result.reference?.page_count ?? pages.length
  const candidatePages = result.candidate_page_count ?? result.candidate?.page_count ?? pages.length
  const aggregate = result.document_aggregate
  const selectedRisk = selectedPage ? pageRisk(selectedPage) : 0
  const selectedTone = riskTone(selectedRisk)
  const candidatePageMissing = selectedPage?.candidate_page_number === null || selectedPage?.status === 'missing'
  const referencePageMissing = selectedPage?.reference_page_number === null || selectedPage?.status === 'added'
  const candidatePageNumber = candidatePageMissing
    ? null
    : selectedPage?.candidate_page_number ?? selectedPage?.page_number ?? null
  const referencePageNumber = referencePageMissing
    ? null
    : selectedPage?.reference_page_number ?? selectedPage?.page_number ?? null

  const switchPage = (pageNumber: number) => {
    if (pageNumber === selectedPageNumber) return
    setSelectedPageNumber(pageNumber)
    onClearFinding()
  }

  const selectFinding = (finding: Finding) => {
    setSelectedPageNumber(finding.page_number)
    onSelectFinding(finding)
  }

  return (
    <motion.main
      className="result-layout"
      initial={reduceMotion ? false : { opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: reduceMotion ? 0 : 0.4 }}
    >
      <div className="result-titlebar">
        <div>
          <span className="eyebrow"><CheckIcon /> Analysis complete · {modeDescription[result.comparison_mode].title}</span>
          <h1>Evidence review</h1>
          <p>Move between pages or select any finding to open its page-specific evidence.</p>
        </div>
        <div className="result-actions">
          <button className="button button--ghost button--compact" type="button" onClick={onReset}>
            Analyse another pair
          </button>
          <button className="button button--secondary button--compact" type="button" onClick={onDemoAgain}>
            <RefreshIcon /> Demo again
          </button>
        </div>
      </div>

      <section className={`risk-summary risk-summary--${tone}`} aria-label="Analysis assessment">
        <div className="risk-gauge" style={{ '--risk': `${result.overall_tampering_risk * 3.6}deg` } as CSSProperties}>
          <div>
            <strong>{Math.round(result.overall_tampering_risk)}</strong>
            <span>/ 100</span>
          </div>
        </div>
        <div className="risk-summary__copy">
          <span>Document tampering risk</span>
          <h2>{result.risk_label}</h2>
          <p>
            {result.finding_count
              ? `${result.finding_count} ${result.finding_count === 1 ? 'region differs' : 'regions differ'} across ${totalPages} ${totalPages === 1 ? 'page' : 'pages'} and should be reviewed.`
              : `No meaningful altered regions were localized across ${totalPages} ${totalPages === 1 ? 'page' : 'pages'}.`}
          </p>
        </div>
        <div className="risk-metrics">
          <Metric label="Assessment confidence" value={result.assessment_confidence} />
          <Metric label="Analysis coverage" value={result.analysis_coverage} />
          <Metric label="Alignment quality" value={result.alignment_quality} />
          <Metric label="Processing time" value={result.processing_duration_ms} displayValue={formatDuration(result.processing_duration_ms)} />
        </div>
      </section>

      <section className="result-context" aria-label="Document aggregate">
        <article className="mode-summary">
          <span className="mode-summary__icon"><ShieldIcon /></span>
          <div>
            <small>Comparison mode</small>
            <strong>{modeDescription[result.comparison_mode].title}</strong>
            <p>{modeDescription[result.comparison_mode].detail}</p>
          </div>
        </article>
        <article className="document-aggregate">
          <span><small>Reference</small><strong>{referencePages} {referencePages === 1 ? 'page' : 'pages'}</strong></span>
          <span><small>Candidate</small><strong>{candidatePages} {candidatePages === 1 ? 'page' : 'pages'}</strong></span>
          <span><small>Review pages</small><strong>{aggregate?.reviewed_page_count ?? pages.filter((page) => pageFindingCount(page) > 0).length}</strong></span>
          <span><small>Variable regions</small><strong>{result.region_suggestions?.length ?? 0}</strong></span>
        </article>
      </section>

      <PageAnomalySummary anomalies={anomalies} />

      <section className="page-filmstrip" aria-label="Document pages">
        <div className="page-filmstrip__heading">
          <div>
            <span className="eyebrow">Page filmstrip</span>
            <h2>Page {selectedPage?.page_number ?? 1} of {totalPages}</h2>
          </div>
          <div className="page-navigation">
            <button
              type="button"
              className="icon-button"
              aria-label="Previous page"
              disabled={selectedPageIndex <= 0}
              onClick={() => switchPage(pages[selectedPageIndex - 1]?.page_number ?? selectedPageNumber)}
            >
              <ChevronIcon className="chevron--back" />
            </button>
            <button
              type="button"
              className="icon-button"
              aria-label="Next page"
              disabled={selectedPageIndex >= pages.length - 1}
              onClick={() => switchPage(pages[selectedPageIndex + 1]?.page_number ?? selectedPageNumber)}
            >
              <ChevronIcon />
            </button>
          </div>
        </div>
        <div className="page-filmstrip__list" role="list">
          {pages.map((page) => {
            const risk = pageRisk(page)
            const pageTone = riskTone(risk)
            const findingCount = pageFindingCount(page)
            const pageStatus = page.status ?? 'matched'
            const anomalous = !['matched', 'completed'].includes(pageStatus)
            return (
              <div role="listitem" className="page-card-item" key={page.page_number}>
                <button
                  type="button"
                  className={`page-card${page.page_number === selectedPage?.page_number ? ' is-selected' : ''}${anomalous ? ' is-anomalous' : ''}`}
                  aria-current={page.page_number === selectedPage?.page_number ? 'page' : undefined}
                  aria-label={`Page ${page.page_number}: ${Math.round(risk)} risk, ${findingCount} ${findingCount === 1 ? 'finding' : 'findings'}${anomalous ? `, ${statusTitle(pageStatus)}` : ''}`}
                  onClick={() => switchPage(page.page_number)}
                >
                  <span className="page-card__media">
                    {page.candidate_image_url ? (
                      <img src={page.candidate_image_url} alt={`Page ${page.page_number} thumbnail`} />
                    ) : (
                      <span><FileIcon /><small>{page.page_number}</small></span>
                    )}
                    <i className={`page-risk-badge page-risk-badge--${pageTone}`}>{Math.round(risk)}</i>
                  </span>
                  <span className="page-card__meta">
                    <strong>Page {String(page.page_number).padStart(2, '0')}</strong>
                    <small>{findingCount} {findingCount === 1 ? 'finding' : 'findings'}</small>
                  </span>
                  {anomalous && <span className={`page-status page-status--${pageStatus}`}>{statusTitle(pageStatus)}</span>}
                </button>
              </div>
            )
          })}
        </div>
      </section>

      <div className="selected-page-summary" aria-label="Selected page assessment">
        <div>
          <span className="eyebrow">Selected-page assessment</span>
          <strong>Page {selectedPage?.page_number ?? 1}</strong>
        </div>
        <span className={`risk-chip risk-chip--${selectedTone}`}>{Math.round(selectedRisk)} risk</span>
        <span>{currentPageFindings.length} {currentPageFindings.length === 1 ? 'finding' : 'findings'}</span>
        <span>{statusTitle(selectedPage?.status ?? 'matched')}</span>
        <span>
          {selectedPage?.ocr?.provider
            ? `Text · ${selectedPage.ocr.provider}${selectedPage.ocr.succeeded === false ? ' (failed)' : ''}`
            : 'Text source · visual / embedded'}
        </span>
        {suggestions.length > 0 && <span className="variable-region-key"><i /> {suggestions.length} suggested variable {suggestions.length === 1 ? 'region' : 'regions'}</span>}
      </div>

      <div className="result-grid">
        <div className="document-comparison" aria-label="Selected page comparison">
          <DocumentViewer
            imageUrl={candidatePageMissing ? undefined : selectedPage?.candidate_image_url}
            width={selectedPage?.width}
            height={selectedPage?.height}
            findings={candidatePageMissing ? [] : currentPageFindings}
            regionSuggestions={candidatePageMissing ? [] : suggestions}
            selectedFindingId={selectedFinding?.finding_id}
            onSelectFinding={selectFinding}
            pageNumber={candidatePageNumber}
            totalPages={candidatePages}
            pageStatus={selectedPage?.status}
            side="candidate"
            pageMissing={candidatePageMissing}
            label={`Questioned document · page ${selectedPage?.page_number ?? 1}`}
          />
          <DocumentViewer
            imageUrl={referencePageMissing ? undefined : selectedPage?.reference_image_url}
            width={selectedPage?.width}
            height={selectedPage?.height}
            pageNumber={referencePageNumber}
            totalPages={referencePages}
            pageStatus={selectedPage?.status}
            side="reference"
            pageMissing={referencePageMissing}
            label={`Trusted reference · page ${selectedPage?.page_number ?? 1}`}
          />
        </div>

        <aside className="findings-panel">
          <div className="findings-panel__heading">
            <div>
              <span className="eyebrow">Document evidence</span>
              <h2>{result.finding_count} {result.finding_count === 1 ? 'finding' : 'findings'}</h2>
            </div>
            <span className={`risk-chip risk-chip--${tone}`}>{tone}</span>
          </div>
          {allFindings.length ? (
            <div className="finding-list">
              {allFindings.map((finding, index) => (
                <button
                  type="button"
                  key={finding.finding_id}
                  className={`finding-card${selectedFinding?.finding_id === finding.finding_id ? ' is-selected' : ''}${finding.page_number === selectedPage?.page_number ? ' is-on-page' : ''}`}
                  aria-label={`Open finding on page ${finding.page_number}: ${finding.title}`}
                  onClick={() => selectFinding(finding)}
                >
                  <span className="finding-card__index">{String(index + 1).padStart(2, '0')}</span>
                  <span className="finding-card__copy">
                    <span>{finding.title}</span>
                    <small>{finding.explanation}</small>
                    <span className="finding-card__meta">
                      <i className={`severity severity--${finding.severity.toLowerCase()}`}>{finding.severity}</i>
                      <i>Page {finding.page_number}</i>
                      <i>{Math.round(finding.confidence_score)}% confidence</i>
                    </span>
                  </span>
                  <ChevronIcon className="finding-card__chevron" />
                </button>
              ))}
            </div>
          ) : (
            <div className="empty-findings">
              <CheckIcon />
              <strong>No localized differences</strong>
              <p>The comparison did not produce evidence regions above the reporting threshold.</p>
            </div>
          )}
          <div className="result-integrity-note">
            <ShieldIcon />
            <p><strong>Explainable assessment</strong><span>Scores indicate forensic risk, not a legal authenticity judgment.</span></p>
          </div>
        </aside>
      </div>
    </motion.main>
  )
}

function ErrorScreen({ error, onReset, onRetryDemo }: { error: AnalysisError; onReset: () => void; onRetryDemo: () => void }) {
  return (
    <main className="error-layout" role="alert">
      <div className="error-card">
        <span className="error-card__icon"><AlertIcon /></span>
        <span className="eyebrow">Analysis interrupted</span>
        <h1>We couldn’t complete this comparison.</h1>
        <p>{error.message}</p>
        <code>{error.code}</code>
        <div className="error-card__actions">
          <button type="button" className="button button--primary" onClick={onReset}>Return to upload</button>
          <button type="button" className="button button--ghost" onClick={onRetryDemo}><RefreshIcon /> Try synthetic demo</button>
        </div>
      </div>
    </main>
  )
}

export default function App() {
  const [screen, setScreen] = useState<Screen>('upload')
  const [reference, setReference] = useState<File | null>(null)
  const [candidate, setCandidate] = useState<File | null>(null)
  const [comparisonMode, setComparisonMode] = useState<ComparisonMode>('exact')
  const [candidatePreview, setCandidatePreview] = useState<string>()
  const [pagePreviews, setPagePreviews] = useState<PagePreviewMap>({})
  const [submitting, setSubmitting] = useState(false)
  const [progress, setProgress] = useState<ProgressEvent>(initialProgress)
  const [connection, setConnection] = useState<ConnectionState>('connecting')
  const [result, setResult] = useState<DocumentResult | null>(null)
  const [error, setError] = useState<AnalysisError | null>(null)
  const [jobId, setJobId] = useState('')
  const [selectedFinding, setSelectedFinding] = useState<Finding | null>(null)
  const watchCleanup = useRef<(() => void) | null>(null)

  useEffect(() => {
    // Uploaded PDFs are never passed to a browser PDF parser. The viewer waits
    // for the backend's validated, script-free candidate-page PNG.
    if (!candidate || isPdf(candidate) || typeof URL.createObjectURL !== 'function') {
      setCandidatePreview(undefined)
      return undefined
    }
    const preview = URL.createObjectURL(candidate)
    setCandidatePreview(preview)
    return () => URL.revokeObjectURL(preview)
  }, [candidate])

  useEffect(() => () => watchCleanup.current?.(), [])

  const reset = useCallback(() => {
    watchCleanup.current?.()
    watchCleanup.current = null
    setScreen('upload')
    setReference(null)
    setCandidate(null)
    setComparisonMode('exact')
    setPagePreviews({})
    setProgress(initialProgress)
    setConnection('connecting')
    setResult(null)
    setError(null)
    setJobId('')
    setSelectedFinding(null)
    setSubmitting(false)
  }, [])

  const watchJob = useCallback((created: AnalysisJobCreated) => {
    setJobId(created.job_id)
    watchCleanup.current?.()
    watchCleanup.current = watchAnalysis(created, {
      onConnection: setConnection,
      onProgress: (event) => {
        if (event.candidate_page_url) {
          setPagePreviews((current) => ({ ...current, [event.page_number]: event.candidate_page_url as string }))
        }
        setProgress((current) => {
          const eventIsCurrent = event.progress > current.progress ||
            event.page_number > current.page_number ||
            (event.progress === current.progress && event.page_number >= current.page_number)
          if (!eventIsCurrent) return current
          return {
            ...event,
            progress: Math.max(current.progress, event.progress),
            candidate_page_url: event.candidate_page_url ??
              (event.page_number === current.page_number ? current.candidate_page_url : undefined),
          }
        })
      },
      onComplete: (documentResult) => {
        setResult(documentResult)
        setComparisonMode(documentResult.comparison_mode)
        setProgress((current) => ({ ...current, stage_id: 'complete', progress: 100 }))
        setScreen('complete')
        setSubmitting(false)
      },
      onError: (analysisError) => {
        setError(analysisError)
        setScreen('error')
        setSubmitting(false)
      },
    })
  }, [])

  const startUpload = useCallback(async () => {
    if (!reference || !candidate || submitting) return
    setSubmitting(true)
    setError(null)
    setPagePreviews({})
    setProgress(initialProgress)
    setScreen('analysis')
    try {
      watchJob(await createAnalysis(reference, candidate, comparisonMode))
    } catch (requestError) {
      setError(errorFromUnknown(requestError))
      setScreen('error')
      setSubmitting(false)
    }
  }, [candidate, comparisonMode, reference, submitting, watchJob])

  const startDemo = useCallback(async () => {
    if (submitting) return
    watchCleanup.current?.()
    setReference(null)
    setCandidate(null)
    setCandidatePreview(undefined)
    setComparisonMode('exact')
    setPagePreviews({})
    setSubmitting(true)
    setResult(null)
    setSelectedFinding(null)
    setError(null)
    setProgress({ ...initialProgress, message: 'Loading deterministic synthetic documents' })
    setConnection('connecting')
    setScreen('analysis')
    try {
      watchJob(await runDemo())
    } catch (requestError) {
      setError(errorFromUnknown(requestError))
      setScreen('error')
      setSubmitting(false)
    }
  }, [submitting, watchJob])

  const findingIndex = result && selectedFinding
    ? Math.max(0, result.findings.findIndex((item) => item.finding_id === selectedFinding.finding_id))
    : 0
  const selectedFindingPage = result && selectedFinding
    ? result.pages.find((page) => page.page_number === selectedFinding.page_number)
    : undefined
  const candidateEvidenceAvailable = selectedFindingPage
    ? selectedFindingPage.candidate_page_number !== null && selectedFindingPage.status !== 'missing'
    : true
  const referenceEvidenceAvailable = selectedFindingPage
    ? selectedFindingPage.reference_page_number !== null && selectedFindingPage.status !== 'added'
    : true

  return (
    <div className="app-shell">
      <div className="ambient ambient--one" aria-hidden="true" />
      <div className="ambient ambient--two" aria-hidden="true" />
      <AppHeader quiet={screen !== 'upload'} />
      <AnimatePresence mode="wait">
        {screen === 'upload' && (
          <UploadScreen
            key="upload"
            reference={reference}
            candidate={candidate}
            comparisonMode={comparisonMode}
            onReference={setReference}
            onCandidate={setCandidate}
            onMode={setComparisonMode}
            onStart={() => void startUpload()}
            onDemo={() => void startDemo()}
            submitting={submitting}
          />
        )}
        {screen === 'analysis' && (
          <AnalysisScreen
            key="analysis"
            progress={progress}
            connection={connection}
            candidatePreview={candidatePreview}
            pagePreviews={pagePreviews}
            jobId={jobId}
          />
        )}
        {screen === 'complete' && result && (
          <ResultScreen
            key="complete"
            result={result}
            selectedFinding={selectedFinding}
            onSelectFinding={setSelectedFinding}
            onClearFinding={() => setSelectedFinding(null)}
            onReset={reset}
            onDemoAgain={() => void startDemo()}
          />
        )}
        {screen === 'error' && error && (
          <ErrorScreen key="error" error={error} onReset={reset} onRetryDemo={() => void startDemo()} />
        )}
      </AnimatePresence>
      <EvidenceDrawer
        finding={selectedFinding}
        index={findingIndex}
        onClose={() => setSelectedFinding(null)}
        candidateEvidenceAvailable={candidateEvidenceAvailable}
        referenceEvidenceAvailable={referenceEvidenceAvailable}
      />
      <footer className="site-footer">
        <span>DOCUVERIFY / PHASE 02</span>
        <span>Page-aware evidence-led comparison</span>
      </footer>
    </div>
  )
}
