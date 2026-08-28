import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react'
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
  ConnectionState,
  DocumentResult,
  Finding,
  ProgressEvent,
  StageId,
} from './types/contracts'

type Screen = 'upload' | 'analysis' | 'complete' | 'error'

const stages: Array<{ id: StageId; label: string }> = [
  { id: 'validating_uploads', label: 'Validate files' },
  { id: 'rendering_documents', label: 'Render pages' },
  { id: 'normalizing_pages', label: 'Normalize' },
  { id: 'aligning_reference', label: 'Align reference' },
  { id: 'extracting_text', label: 'Extract text' },
  { id: 'comparing_structure', label: 'Compare structure' },
  { id: 'localizing_differences', label: 'Localize differences' },
  { id: 'scoring_evidence', label: 'Score evidence' },
  { id: 'preparing_result', label: 'Prepare result' },
]

const stageCopy: Record<string, { title: string; description: string }> = {
  queued: {
    title: 'Preparing the document',
    description: 'The analysis is queued and will begin locally in a moment.',
  },
  validating_uploads: {
    title: 'Validating both documents',
    description: 'Checking file integrity, content type and the single-page boundary.',
  },
  rendering_documents: {
    title: 'Rendering document surfaces',
    description: 'Creating browser-safe page images for precise visual inspection.',
  },
  normalizing_pages: {
    title: 'Normalizing the pages',
    description: 'Matching page scale, orientation and colour space without hiding evidence.',
  },
  aligning_reference: {
    title: 'Aligning with the trusted reference',
    description: 'Registering the two pages so matching content occupies the same coordinates.',
  },
  extracting_text: {
    title: 'Extracting text and labels',
    description: 'Reading available text while keeping visual comparison independent of OCR.',
  },
  comparing_structure: {
    title: 'Comparing document structure',
    description: 'Inspecting layout, edges and pixels for meaningful changes.',
  },
  localizing_differences: {
    title: 'Localizing suspicious differences',
    description: 'Grouping changed pixels into explainable candidate regions.',
  },
  scoring_evidence: {
    title: 'Scoring forensic evidence',
    description: 'Combining alignment, visual and text signals into a bounded risk score.',
  },
  preparing_result: {
    title: 'Preparing the result',
    description: 'Generating evidence crops, overlays and normalized document markers.',
  },
  complete: {
    title: 'Analysis complete',
    description: 'The evidence package is ready for review.',
  },
}

const initialProgress: ProgressEvent = {
  event_id: '',
  job_id: '',
  stage_id: 'queued',
  message: 'Preparing the document',
  progress: 0,
  page_number: 1,
  total_pages: 1,
  timestamp: '',
  finding_count: 0,
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

function AppHeader({ quiet = false }: { quiet?: boolean }) {
  return (
    <header className={`site-header${quiet ? ' site-header--quiet' : ''}`}>
      <button className="brand" type="button" onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}>
        <span className="brand__mark"><ShieldIcon /></span>
        <span>DOCU<span>VERIFY</span></span>
      </button>
      <div className="header-context">
        <span className="header-context__line" />
        <span>Phase 01</span>
        <span className="local-badge"><span /> Local analysis</span>
      </div>
    </header>
  )
}

function UploadScreen({
  reference,
  candidate,
  onReference,
  onCandidate,
  onStart,
  onDemo,
  submitting,
}: {
  reference: File | null
  candidate: File | null
  onReference: (file: File | null) => void
  onCandidate: (file: File | null) => void
  onStart: () => void
  onDemo: () => void
  submitting: boolean
}) {
  const reduceMotion = useReducedMotion()
  const ready = Boolean(reference && candidate)

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
            <span className="eyebrow-number">01 / COMPARE</span>
          </div>
          <h1>
            Upload the document.<br />
            See the <em>evidence.</em><br />
            Trust what can be explained.
          </h1>
          <p>
            Compare a questioned document against a trusted reference. DocuVerify aligns,
            inspects and explains every region it flags—right on your machine.
          </p>
          <div className="hero-proof">
            <span><CheckIcon /> Pixel-level comparison</span>
            <span><CheckIcon /> Localized evidence</span>
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
          <span className="motif-coordinate motif-coordinate--one">0.742 / 0.381</span>
          <span className="motif-coordinate motif-coordinate--two">CONF. 98.2</span>
        </div>
      </section>

      <section className="upload-workbench" aria-labelledby="upload-title">
        <div className="workbench-header">
          <div>
            <span className="eyebrow">Trusted-reference verification</span>
            <h2 id="upload-title">Compare two documents</h2>
          </div>
          <div className="mode-state"><span /> Exact document mode</div>
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

        <div className="comparison-row">
          <label className="mode-option">
            <input type="radio" name="mode" value="exact" checked readOnly />
            <span className="mode-option__radio"><span /></span>
            <span>
              <strong>Exact comparison</strong>
              <small>Page geometry, text and visual evidence</small>
            </span>
            <span className="mode-option__tag">ACTIVE</span>
          </label>
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

function AnalysisScreen({
  progress,
  connection,
  candidatePreview,
  jobId,
}: {
  progress: ProgressEvent
  connection: ConnectionState
  candidatePreview?: string
  jobId: string
}) {
  const reduceMotion = useReducedMotion()
  const copy = stageCopy[progress.stage_id] ?? {
    title: progress.message || 'Analysing document',
    description: 'The local forensic pipeline is processing the current stage.',
  }
  const activeIndex = currentStageIndex(progress.stage_id)

  return (
    <motion.main
      className="analysis-layout"
      initial={reduceMotion ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: reduceMotion ? 0 : 0.35 }}
    >
      <div className="analysis-titlebar">
        <div>
          <span className="eyebrow">Live forensic analysis</span>
          <h1>{copy.title}</h1>
          <p>{progress.message || copy.description}</p>
        </div>
        <div className="analysis-titlebar__status">
          <ConnectionBadge state={connection} />
          <span className="job-reference">JOB {jobId ? jobId.slice(0, 8).toUpperCase() : 'PENDING'}</span>
        </div>
      </div>

      <div className="analysis-grid">
        <DocumentViewer
          imageUrl={progress.candidate_page_url || candidatePreview}
          scanning
          progress={progress.progress}
        />

        <aside className="analysis-sidebar">
          <section className="progress-panel">
            <div className="progress-panel__top">
              <span>Analysis progress</span>
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
            <div className="current-stage-card">
              <span className="current-stage-card__icon"><ScanIcon /></span>
              <div>
                <span>Current stage</span>
                <strong>{copy.title}</strong>
                <p>{copy.description}</p>
              </div>
            </div>
          </section>

          <section className="pipeline-panel">
            <div className="panel-label"><span>Forensic pipeline</span><small>REAL-TIME</small></div>
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
            <small>Findings appear only when the backend confirms them.</small>
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

function ResultScreen({
  result,
  selectedFinding,
  onSelectFinding,
  onReset,
  onDemoAgain,
}: {
  result: DocumentResult
  selectedFinding: Finding | null
  onSelectFinding: (finding: Finding) => void
  onReset: () => void
  onDemoAgain: () => void
}) {
  const reduceMotion = useReducedMotion()
  const tone = riskTone(result.overall_tampering_risk)
  const page = result.pages[0]
  const findings = page?.findings.length ? page.findings : result.findings
  const imageUrl = page?.candidate_image_url || result.candidate?.preview_url

  return (
    <motion.main
      className="result-layout"
      initial={reduceMotion ? false : { opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: reduceMotion ? 0 : 0.4 }}
    >
      <div className="result-titlebar">
        <div>
          <span className="eyebrow"><CheckIcon /> Analysis complete</span>
          <h1>Evidence review</h1>
          <p>Select a marker to inspect the visual evidence and measurements behind this assessment.</p>
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
          <span>Tampering risk</span>
          <h2>{result.risk_label}</h2>
          <p>
            {result.finding_count
              ? `${result.finding_count} ${result.finding_count === 1 ? 'region differs' : 'regions differ'} from the trusted reference and should be reviewed.`
              : 'No meaningful altered regions were localized in this comparison.'}
          </p>
        </div>
        <div className="risk-metrics">
          <Metric label="Assessment confidence" value={result.assessment_confidence} />
          <Metric label="Analysis coverage" value={result.analysis_coverage} />
          <Metric label="Alignment quality" value={result.alignment_quality} />
          <Metric label="Processing time" value={result.processing_duration_ms} displayValue={formatDuration(result.processing_duration_ms)} />
        </div>
      </section>

      <div className="result-grid">
        <DocumentViewer
          imageUrl={imageUrl}
          width={page?.width}
          height={page?.height}
          findings={findings}
          selectedFindingId={selectedFinding?.finding_id}
          onSelectFinding={onSelectFinding}
        />

        <aside className="findings-panel">
          <div className="findings-panel__heading">
            <div>
              <span className="eyebrow">Localized evidence</span>
              <h2>{result.finding_count} {result.finding_count === 1 ? 'finding' : 'findings'}</h2>
            </div>
            <span className={`risk-chip risk-chip--${tone}`}>{tone}</span>
          </div>
          {findings.length ? (
            <div className="finding-list">
              {findings.map((finding, index) => (
                <button
                  type="button"
                  key={finding.finding_id}
                  className={`finding-card${selectedFinding?.finding_id === finding.finding_id ? ' is-selected' : ''}`}
                  onClick={() => onSelectFinding(finding)}
                >
                  <span className="finding-card__index">{String(index + 1).padStart(2, '0')}</span>
                  <span className="finding-card__copy">
                    <span>{finding.title}</span>
                    <small>{finding.explanation}</small>
                    <span className="finding-card__meta">
                      <i className={`severity severity--${finding.severity.toLowerCase()}`}>{finding.severity}</i>
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
  const [candidatePreview, setCandidatePreview] = useState<string>()
  const [submitting, setSubmitting] = useState(false)
  const [progress, setProgress] = useState<ProgressEvent>(initialProgress)
  const [connection, setConnection] = useState<ConnectionState>('connecting')
  const [result, setResult] = useState<DocumentResult | null>(null)
  const [error, setError] = useState<AnalysisError | null>(null)
  const [jobId, setJobId] = useState('')
  const [selectedFinding, setSelectedFinding] = useState<Finding | null>(null)
  const watchCleanup = useRef<(() => void) | null>(null)

  useEffect(() => {
    const isPdf = candidate?.type === 'application/pdf' || candidate?.name.toLowerCase().endsWith('.pdf')
    // Uploaded PDFs are never passed to the browser PDF parser. The viewer
    // waits for the backend's validated, script-free candidate-page PNG.
    if (!candidate || isPdf || typeof URL.createObjectURL !== 'function') {
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
      onProgress: (event) => setProgress((current) => (
        event.progress >= current.progress
          ? {
              ...event,
              candidate_page_url: event.candidate_page_url ?? current.candidate_page_url,
            }
          : current
      )),
      onComplete: (documentResult) => {
        setResult(documentResult)
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
    setProgress(initialProgress)
    setScreen('analysis')
    try {
      watchJob(await createAnalysis(reference, candidate, 'exact'))
    } catch (requestError) {
      setError(errorFromUnknown(requestError))
      setScreen('error')
      setSubmitting(false)
    }
  }, [candidate, reference, submitting, watchJob])

  const startDemo = useCallback(async () => {
    if (submitting) return
    watchCleanup.current?.()
    // A demo is a new document pair. Clear any upload-backed object URL so the
    // live viewer never presents an unrelated candidate while the fixture is
    // being rendered by the backend.
    setReference(null)
    setCandidate(null)
    setCandidatePreview(undefined)
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
            onReference={setReference}
            onCandidate={setCandidate}
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
            jobId={jobId}
          />
        )}
        {screen === 'complete' && result && (
          <ResultScreen
            key="complete"
            result={result}
            selectedFinding={selectedFinding}
            onSelectFinding={setSelectedFinding}
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
        index={result?.findings.findIndex((item) => item.finding_id === selectedFinding?.finding_id) ?? 0}
        onClose={() => setSelectedFinding(null)}
      />
      <footer className="site-footer">
        <span>DOCUVERIFY / PHASE 01</span>
        <span>Evidence-led document comparison</span>
      </footer>
    </div>
  )
}
