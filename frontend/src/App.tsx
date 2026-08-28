import {
  useCallback,
  useEffect,
  useRef,
  useState,
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

const conciseStageLabel = (stageId: StageId): string => {
  if (['queued', 'validating_uploads', 'rendering_documents', 'normalizing_pages'].includes(stageId)) {
    return 'Preparing'
  }
  if (stageId === 'extracting_text') return 'Reading'
  if (stageId === 'aligning_reference') return 'Aligning'
  if (['identifying_regions', 'comparing_structure', 'comparing_typography'].includes(stageId)) {
    return 'Comparing'
  }
  if (['localizing_differences', 'scoring_evidence'].includes(stageId)) return 'Locating evidence'
  if (['aggregating_document', 'preparing_result', 'complete'].includes(stageId)) return 'Finalizing'
  return 'Preparing'
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

const modeDescription: Record<ComparisonMode, { title: string; detail: string }> = {
  exact: {
    title: 'Exact',
    detail: 'Every page and field should match.',
  },
  template: {
    title: 'Template',
    detail: 'Expected field values may vary.',
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

function AppHeader({ quiet = false }: { quiet?: boolean }) {
  return (
    <header className={`site-header${quiet ? ' site-header--quiet' : ''}`}>
      <button className="brand" type="button" onClick={() => window.scrollTo({ top: 0 })}>
        <span className="brand__mark"><ShieldIcon /></span>
        <span>DOCU<strong>VERIFY</strong></span>
      </button>
      <div className="header-context">
        <span>Document verification</span>
        <span className="local-badge"><span /> Ready</span>
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

  return (
    <motion.main
      className="upload-layout"
      initial={reduceMotion ? false : { opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: reduceMotion ? 0 : 0.45 }}
    >
      <section className="upload-intro">
        <span className="section-kicker">Document comparison</span>
        <h1>Verify a document</h1>
        <p>Compare a questioned document with a trusted reference.</p>
      </section>

      <section className="upload-workbench" aria-labelledby="upload-title">
        <div className="workbench-header">
          <div>
            <span className="section-kicker">Comparison setup</span>
            <h2 id="upload-title">Documents</h2>
          </div>
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
                <span className="mode-option__copy">
                  <strong>{modeDescription[mode].title}</strong>
                  <small>{modeDescription[mode].detail}</small>
                </span>
              </label>
            ))}
          </fieldset>
        </div>

        <div className="dropzone-grid">
          <FileDropzone
            id="reference"
            eyebrow="Trusted reference"
            title="Add the known-good file"
            description="Drop a file here or choose from this device"
            file={reference}
            onFile={onReference}
            tone="reference"
          />
          <div className="compare-bridge" aria-hidden="true"><span>→</span></div>
          <FileDropzone
            id="candidate"
            eyebrow="Questioned document"
            title="Add the file to verify"
            description="Drop a file here or choose from this device"
            file={candidate}
            onFile={onCandidate}
            tone="candidate"
          />
        </div>

        <div className="comparison-row">
          <span className="format-note">PDF, PNG or JPEG</span>
          <div className="action-group">
            <button className="button button--ghost" type="button" onClick={onDemo} disabled={submitting}>
              <SparkIcon /> {submitting ? 'Starting…' : 'Try demo'}
            </button>
            <button className="button button--primary" type="button" onClick={onStart} disabled={!ready || submitting}>
              {submitting ? 'Preparing…' : 'Analyze document'} <ArrowIcon />
            </button>
          </div>
        </div>

        <details className="analysis-details upload-details">
          <summary>Analysis details</summary>
          <div>
            <p>PDFs may contain up to 10 pages. Embedded text is used when available; local raster OCR is used when needed.</p>
            <p>Files are processed by the local service and retained only for the configured cleanup window.</p>
          </div>
        </details>
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

function ScannerPaper({
  imageUrl,
  pageNumber,
  totalPages,
  localizedRegion,
}: {
  imageUrl?: string
  pageNumber: number
  totalPages: number
  localizedRegion?: ProgressEvent['localized_region']
}) {
  return (
    <section className="scanner-card" aria-label="Questioned document viewer">
      <div className="scanner-card__header">
        <span>Questioned document</span>
        <span>Page {pageNumber} / {totalPages}</span>
      </div>
      <div className="scanner-stage">
        <div className="scanner-frame" aria-hidden="true" />
        <div className="scanner-paper">
          {imageUrl ? (
            <img src={imageUrl} alt="Questioned document" />
          ) : (
            <div className="scanner-paper__placeholder" data-testid="document-placeholder" aria-hidden="true">
              <svg viewBox="0 0 420 594" focusable="false">
                <rect x="54" y="58" width="98" height="14" rx="7" />
                <rect x="54" y="91" width="286" height="6" rx="3" />
                <rect x="54" y="109" width="238" height="6" rx="3" />
                <rect x="54" y="157" width="312" height="8" rx="4" />
                <rect x="54" y="181" width="280" height="8" rx="4" />
                <rect x="54" y="205" width="304" height="8" rx="4" />
                <rect x="54" y="263" width="132" height="64" rx="8" />
                <rect x="207" y="263" width="159" height="8" rx="4" />
                <rect x="207" y="287" width="137" height="8" rx="4" />
                <rect x="207" y="311" width="151" height="8" rx="4" />
                <rect x="54" y="389" width="312" height="8" rx="4" />
                <rect x="54" y="413" width="260" height="8" rx="4" />
                <rect x="54" y="437" width="294" height="8" rx="4" />
              </svg>
            </div>
          )}
          <div className="scanner-sweep" aria-hidden="true"><span /></div>
          {localizedRegion && (
            <span
              className="localized-region-preview"
              aria-hidden="true"
              style={{
                left: `${localizedRegion.x * 100}%`,
                top: `${localizedRegion.y * 100}%`,
                width: `${localizedRegion.width * 100}%`,
                height: `${localizedRegion.height * 100}%`,
              }}
            />
          )}
        </div>
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
  const currentPage = Math.min(progress.total_pages, Math.max(1, progress.page_number))
  const currentPreview = progress.candidate_page_url || pagePreviews[currentPage] ||
    (currentPage === 1 ? candidatePreview : undefined)
  const stageLabel = conciseStageLabel(progress.stage_id)

  return (
    <motion.main
      className="analysis-layout"
      initial={reduceMotion ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: reduceMotion ? 0 : 0.35 }}
    >
      <div className="analysis-titlebar">
        <div>
          <span className="section-kicker">Analysis in progress</span>
          <h1>Scanning page {currentPage} of {progress.total_pages}</h1>
        </div>
        <span className="analysis-percentage">{Math.round(progress.progress)}%</span>
      </div>

      <div className="analysis-grid">
        <ScannerPaper
          imageUrl={currentPreview}
          pageNumber={currentPage}
          totalPages={progress.total_pages}
          localizedRegion={progress.localized_region}
        />

        <aside className="analysis-sidebar">
          <section className="progress-panel">
            <div className="scan-stage-label">
              <span className="scan-stage-label__icon"><ScanIcon /></span>
              <div>
                <span>Current stage</span>
                <strong>{stageLabel}</strong>
              </div>
            </div>
            <p className="visually-hidden" aria-live="polite" aria-atomic="true">
              {stageLabel}. Scanning page {currentPage} of {progress.total_pages}. {Math.round(progress.progress)} percent complete.
              {progress.finding_count > 0 ? ` ${progress.finding_count} ${progress.finding_count === 1 ? 'finding' : 'findings'}.` : ''}
            </p>
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
            <div className="progress-caption">
              <span>{Math.round(progress.progress)}% complete</span>
              {progress.finding_count > 0 && (
                <span>{progress.finding_count} {progress.finding_count === 1 ? 'finding' : 'findings'}</span>
              )}
            </div>
          </section>

          <details className="analysis-details progress-details">
            <summary>Analysis details</summary>
            <dl>
              <div><dt>Connection</dt><dd><ConnectionBadge state={connection} /></dd></div>
              <div><dt>Job</dt><dd>{jobId ? jobId.slice(0, 8).toUpperCase() : 'Pending'}</dd></div>
              <div><dt>Page event</dt><dd>{progress.page_stage || progress.message || progress.stage_id}</dd></div>
              <div><dt>Text engine</dt><dd>{progress.ocr_provider || 'Detecting source'}{progress.ocr_device ? ` · ${progress.ocr_device.toUpperCase()}` : ''}</dd></div>
            </dl>
          </details>
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
          <span className="section-kicker"><CheckIcon /> Analysis complete · {modeDescription[result.comparison_mode].title}</span>
          <h1>Evidence review</h1>
        </div>
        <div className="result-actions">
          <button className="button button--ghost button--compact" type="button" onClick={onReset}>
            New comparison
          </button>
          <button className="button button--secondary button--compact" type="button" onClick={onDemoAgain}>
            <RefreshIcon /> Run demo
          </button>
        </div>
      </div>

      <section className={`risk-summary risk-summary--${tone}`} aria-label="Analysis assessment">
        <div className="risk-score">
          <span>Risk</span>
          <strong>{Math.round(result.overall_tampering_risk)}</strong>
          <small>/100</small>
        </div>
        <div className="risk-summary__copy">
          <h2>{result.risk_label}</h2>
          <p>{result.finding_count} {result.finding_count === 1 ? 'finding' : 'findings'} · {modeDescription[result.comparison_mode].title}</p>
        </div>
        <div className="risk-metrics">
          <Metric label="Confidence" value={result.assessment_confidence} />
          <Metric label="Coverage" value={result.analysis_coverage} />
        </div>
      </section>

      <PageAnomalySummary anomalies={anomalies} />

      <section className="page-filmstrip" aria-label="Document pages">
        <div className="page-filmstrip__heading">
          <div>
            <span className="section-kicker">Pages</span>
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
          <span>Selected page</span>
          <strong>Page {selectedPage?.page_number ?? 1}</strong>
        </div>
        <span className={`risk-chip risk-chip--${selectedTone}`}>Risk {Math.round(selectedRisk)}</span>
        <span>{currentPageFindings.length} {currentPageFindings.length === 1 ? 'finding' : 'findings'}</span>
        <span>{statusTitle(selectedPage?.status ?? 'matched')}</span>
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
            <h2>Findings</h2>
            <span>{result.finding_count} total</span>
          </div>
          {allFindings.length ? (
            <div className="finding-list">
              {allFindings.map((finding, index) => (
                <button
                  type="button"
                  key={finding.finding_id}
                  className={`finding-card${selectedFinding?.finding_id === finding.finding_id ? ' is-selected' : ''}${finding.page_number === selectedPage?.page_number ? ' is-on-page' : ''}`}
                  aria-label={`View evidence on page ${finding.page_number}: ${finding.title}`}
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
                    <span className="finding-card__action">View evidence</span>
                  </span>
                  <ChevronIcon className="finding-card__chevron" />
                </button>
              ))}
            </div>
          ) : (
            <div className="empty-findings">
              <CheckIcon />
              <strong>No findings</strong>
              <p>No reportable differences found.</p>
            </div>
          )}
          <div className="result-integrity-note">
            <ShieldIcon />
            <p>Investigative indicator—not proof of authenticity</p>
          </div>
        </aside>
      </div>

      <details className="analysis-details result-details">
        <summary>Analysis details</summary>
        <div className="result-context" aria-label="Document aggregate">
          <dl>
            <div><dt>Mode</dt><dd>{modeDescription[result.comparison_mode].title} — {modeDescription[result.comparison_mode].detail}</dd></div>
            <div><dt>Trusted reference</dt><dd>{referencePages} {referencePages === 1 ? 'page' : 'pages'}</dd></div>
            <div><dt>Questioned document</dt><dd>{candidatePages} {candidatePages === 1 ? 'page' : 'pages'}</dd></div>
            <div><dt>Review pages</dt><dd>{aggregate?.reviewed_page_count ?? pages.filter((page) => pageFindingCount(page) > 0).length}</dd></div>
            <div><dt>Variable regions</dt><dd>{result.region_suggestions?.length ?? 0}</dd></div>
            <div><dt>Alignment</dt><dd>{Math.round(result.alignment_quality)}%</dd></div>
            <div><dt>Processing time</dt><dd>{formatDuration(result.processing_duration_ms)}</dd></div>
            <div><dt>Selected-page text</dt><dd>{selectedPage?.ocr?.provider
              ? `${selectedPage.ocr.provider}${selectedPage.ocr.device ? ` · ${selectedPage.ocr.device.toUpperCase()}` : ''}${selectedPage.ocr.succeeded === false ? ' · failed' : ''}`
              : 'Visual or embedded text'}</dd></div>
            <div><dt>Evidence coordinates</dt><dd>Normalized 0–1</dd></div>
          </dl>
        </div>
      </details>
    </motion.main>
  )
}

function ErrorScreen({ error, onReset, onRetryDemo }: { error: AnalysisError; onReset: () => void; onRetryDemo: () => void }) {
  return (
    <main className="error-layout" role="alert">
      <div className="error-card">
        <span className="error-card__icon"><AlertIcon /></span>
        <h1>Analysis couldn’t be completed</h1>
        <p>{error.message}</p>
        <div className="error-card__actions">
          <button type="button" className="button button--primary" onClick={onReset}>Back to upload</button>
          <button type="button" className="button button--ghost" onClick={onRetryDemo}><RefreshIcon /> Try demo</button>
        </div>
        <details className="analysis-details">
          <summary>Analysis details</summary>
          <code>{error.code}</code>
        </details>
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
    </div>
  )
}
