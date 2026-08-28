import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { createAnalysis, createAutomaticAnalysis, runDemo, watchAnalysis } from './api/client'
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
  AdvancedEvidenceInputs,
  AnalysisJobCreated,
  ComparisonMode,
  ConnectionState,
  DocumentResult,
  Finding,
  NormalizedBoundingBox,
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
  if (['identifying_document_family', 'searching_trusted_profiles', 'matching_issuer_layout'].includes(stageId)) {
    return 'Finding trusted profile'
  }
  if (stageId === 'decoding_codes') return 'Checking codes'
  if (stageId === 'checking_digital_signatures') return 'Checking digital signatures'
  if (stageId === 'inspecting_metadata') return 'Inspecting metadata'
  if (stageId === 'validating_field_consistency') return 'Validating fields'
  if (stageId === 'comparing_handwriting') return 'Comparing handwriting'
  if (stageId === 'comparing_signatures') return 'Comparing signatures'
  if (['aggregating_document', 'aggregating_evidence', 'preparing_result', 'complete'].includes(stageId)) return 'Finalizing'
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

const modeDescription: Record<ComparisonMode, { title: string; path: string; detail: string }> = {
  exact: {
    title: 'Exact',
    path: 'Compare with issued original',
    detail: 'Every page and field should match.',
  },
  template: {
    title: 'Template',
    path: 'Compare with official template',
    detail: 'Expected field values may vary.',
  },
  docuvault: {
    title: 'DocuVault',
    path: 'Find closest trusted profile',
    detail: 'Search local validated profiles without an uploaded reference.',
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

const boxesOverlap = (first: NormalizedBoundingBox, second: NormalizedBoundingBox): boolean => {
  const overlapWidth = Math.max(
    0,
    Math.min(first.x + first.width, second.x + second.width) - Math.max(first.x, second.x),
  )
  const overlapHeight = Math.max(
    0,
    Math.min(first.y + first.height, second.y + second.height) - Math.max(first.y, second.y),
  )
  const overlapArea = overlapWidth * overlapHeight
  const smallerArea = Math.min(first.width * first.height, second.width * second.height)
  return smallerArea > 0 && overlapArea / smallerArea >= 0.15
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

function EvidenceFilePicker({
  id,
  title,
  hint,
  files,
  minimum,
  onFiles,
}: {
  id: string
  title: string
  hint: string
  files: File[]
  minimum: number
  onFiles: (files: File[]) => void
}) {
  return (
    <div className="evidence-picker">
      <div>
        <label htmlFor={id}>{title}</label>
        <small>{hint}</small>
      </div>
      <label className="evidence-picker__button" htmlFor={id}>
        <FileIcon /> {files.length ? `${files.length} selected` : 'Choose files'}
      </label>
      <input
        id={id}
        type="file"
        multiple
        accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg"
        onChange={(event) => onFiles(Array.from(event.target.files ?? []).slice(0, 5))}
      />
      {files.length > 0 && (
        <div className="evidence-picker__selection">
          <span>{files.map((file) => file.name).join(', ')}</span>
          <button type="button" onClick={() => onFiles([])}>Clear</button>
        </div>
      )}
      {files.length > 0 && files.length < minimum && (
        <p className="field-note field-note--warning">Select at least {minimum} samples to run this comparison.</p>
      )}
    </div>
  )
}

function UploadScreen({
  reference,
  candidate,
  comparisonMode,
  onReference,
  onCandidate,
  handwritingExemplars,
  signatureExemplars,
  profileOverride,
  onHandwritingExemplars,
  onSignatureExemplars,
  onProfileOverride,
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
  handwritingExemplars: File[]
  signatureExemplars: File[]
  profileOverride: string
  onHandwritingExemplars: (files: File[]) => void
  onSignatureExemplars: (files: File[]) => void
  onProfileOverride: (profileId: string) => void
  onMode: (mode: ComparisonMode) => void
  onStart: () => void
  onDemo: () => void
  submitting: boolean
}) {
  const reduceMotion = useReducedMotion()
  const signatureEnrollmentValid = signatureExemplars.length === 0 || signatureExemplars.length >= 2
  const ready = Boolean(
    candidate
    && (comparisonMode === 'docuvault' || reference)
    && signatureEnrollmentValid,
  )

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
        <p>Compare with a supplied reference or retrieve the closest validated local profile.</p>
      </section>

      <section className="upload-workbench" aria-labelledby="upload-title">
        <div className="workbench-header">
          <div>
            <span className="section-kicker">Comparison setup</span>
            <h2 id="upload-title">Documents</h2>
          </div>
          <fieldset className="mode-selector" aria-label="Comparison mode">
            <legend className="visually-hidden">Choose a comparison mode</legend>
            {(['exact', 'template', 'docuvault'] as const).map((mode) => (
              <label key={mode} className={`mode-option${comparisonMode === mode ? ' is-selected' : ''}`}>
                <input
                  type="radio"
                  name="mode"
                  value={mode}
                  checked={comparisonMode === mode}
                  onChange={() => onMode(mode)}
                />
                <span className="mode-option__copy">
                  <strong><span className="visually-hidden">{modeDescription[mode].title} </span>{modeDescription[mode].path}</strong>
                  <small>{modeDescription[mode].detail}</small>
                </span>
              </label>
            ))}
          </fieldset>
        </div>

        <div className={`dropzone-grid${comparisonMode === 'docuvault' ? ' dropzone-grid--automatic' : ''}`}>
          {comparisonMode !== 'docuvault' && (
            <>
              <FileDropzone
                id="reference"
                eyebrow={comparisonMode === 'template' ? 'Official template' : 'Issued original'}
                title="Add the trusted reference"
                description="Drop a file here or choose from this device"
                file={reference}
                onFile={onReference}
                tone="reference"
              />
              <div className="compare-bridge" aria-hidden="true"><span>→</span></div>
            </>
          )}
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

        <details className="advanced-inputs">
          <summary>Optional forensic inputs</summary>
          <div className="advanced-inputs__body">
            <p>Trusted exemplars stay local and are used only for this analysis.</p>
            {comparisonMode === 'docuvault' && (
              <label className="profile-override" htmlFor="profile-override">
                <span>Profile override <small>Optional exact local profile ID</small></span>
                <input
                  id="profile-override"
                  value={profileOverride}
                  maxLength={160}
                  placeholder="e.g. issuer.family.v1"
                  onChange={(event) => onProfileOverride(event.target.value.trimStart())}
                />
              </label>
            )}
            <div className="advanced-inputs__grid">
              <EvidenceFilePicker
                id="handwriting-exemplars"
                title="Handwriting exemplars"
                hint="1–5 trusted samples"
                files={handwritingExemplars}
                minimum={1}
                onFiles={onHandwritingExemplars}
              />
              <EvidenceFilePicker
                id="signature-exemplars"
                title="Signature exemplars"
                hint="2–5 trusted samples"
                files={signatureExemplars}
                minimum={2}
                onFiles={onSignatureExemplars}
              />
            </div>
          </div>
        </details>

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

const evidenceLabel = (value: string): string =>
  value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())

const capabilityLabel = (tier: string): string => ({
  metadata_only: 'Metadata only',
  structural: 'Structure and layout',
  visual_reference: 'Trusted visual specimen',
  cryptographic: 'Configured cryptographic capability',
})[tier] ?? evidenceLabel(tier)

const codeStateLabel = (state: string): string => ({
  DETECTED_AND_DECODED: 'QR code detected and decoded',
  DETECTED_BUT_UNREADABLE: 'QR code detected but could not be decoded',
  EXPECTED_REGION_OCCUPIED_UNVERIFIED: 'Expected QR region could not be verified',
  CONFIRMED_MISSING: 'QR code appears absent from the expected region',
  NOT_EXPECTED: 'No QR code is expected for this profile',
  DECODER_UNSUPPORTED: 'The available decoder does not support this code',
  CRYPTOGRAPHIC_VERIFICATION_UNAVAILABLE: 'Cryptographic QR verification is not available for this profile',
})[state] ?? evidenceLabel(state)

function EvidenceSection({
  title,
  status,
  summary,
  children,
}: {
  title: string
  status: string
  summary: string
  children?: React.ReactNode
}) {
  const normalizedStatus = status.toLowerCase()
  const tone = ['failed', 'invalid', 'modified', 'strong_contradictory_evidence'].some((word) => normalizedStatus.includes(word))
    ? 'alert'
    : ['warning', 'unknown', 'limited', 'closest', 'skipped', 'unsupported', 'unsigned', 'not_applicable'].some((word) => normalizedStatus.includes(word))
      ? 'limited'
      : 'clear'
  return (
    <details className={`evidence-section evidence-section--${tone}`}>
      <summary>
        <span><strong>{title}</strong><small>{summary}</small></span>
        <em>{evidenceLabel(status)}</em>
      </summary>
      {children && <div className="evidence-section__body">{children}</div>}
    </details>
  )
}

function EvidenceTechnicalOverview({ result, hideProfile = false }: { result: DocumentResult; hideProfile?: boolean }) {
  const profile = result.reference_profile
  const digital = result.digital_signature
  const codes = result.codes
  const metadata = result.metadata_assessment
  const logical = result.logical_consistency
  const handwriting = result.handwriting
  const signature = result.signature_similarity
  const assessment = result.investigative_assessment
  if (!profile && !digital && !codes && !metadata && !logical && !handwriting && !signature && !assessment) {
    return null
  }
  return (
    <section className="evidence-overview" aria-label="Independent evidence checks">
      <div className="evidence-overview__heading">
        <div>
          <span className="section-kicker">Independent checks</span>
          <h2>Core evidence assessment</h2>
        </div>
        {assessment && <span className="assessment-state">{evidenceLabel(assessment.status)}</span>}
      </div>
      {assessment && (
        <div className="assessment-summary">
          <ShieldIcon />
          <div><strong>{assessment.summary}</strong><small>Deterministic assessment, not an authenticity probability.</small></div>
        </div>
      )}
      <div className="evidence-overview__grid">
        {profile && !hideProfile && (
          <EvidenceSection
            title="Trusted reference profile"
            status={profile.reference_strength}
            summary={profile.selected_profile
              ? `${profile.selected_profile.issuer} · ${profile.selected_profile.document_family} · ${Math.round(profile.selected_profile.score)} match`
              : profile.explanation}
          >
            <p>{profile.explanation}</p>
            {profile.closest_fallback_used && <p className="evidence-caution">Closest available profile only; treat it as context, not issuer proof.</p>}
            {profile.top_matches.length > 0 && (
              <ol className="profile-match-list">
                {profile.top_matches.map((match) => (
                  <li key={match.profile_id}>
                    <div><strong>{match.issuer}</strong><small>{match.document_family} · {match.provenance_assurance}</small></div>
                    <span>{Math.round(match.score)}</span>
                    <p>{match.explanation}</p>
                    <dl>
                      {Object.entries(match.component_scores).map(([name, score]) => (
                        <div key={name}><dt>{evidenceLabel(name)}</dt><dd>{Math.round(score)}</dd></div>
                      ))}
                    </dl>
                    {match.authoritative_source_url && (
                      <a href={match.authoritative_source_url} target="_blank" rel="noreferrer">Authoritative source</a>
                    )}
                  </li>
                ))}
              </ol>
            )}
          </EvidenceSection>
        )}
        {digital && (
          <EvidenceSection title="Digital PDF signature" status={digital.status} summary={digital.explanation}>
            <p>Trust store: {evidenceLabel(digital.trust_store)} · {digital.signature_count} embedded signature{digital.signature_count === 1 ? '' : 's'}</p>
            {digital.checks.map((check) => (
              <article className="evidence-record" key={check.signature_index}>
                <strong>Signature {check.signature_index} · {evidenceLabel(check.status)}</strong>
                <p>{check.explanation}</p>
                <small>Integrity: {check.cryptographically_intact === undefined ? 'unknown' : check.cryptographically_intact ? 'intact' : 'failed'} · Local trust: {check.signer_locally_trusted === undefined ? 'unknown' : check.signer_locally_trusted ? 'trusted' : 'not established'} · Updates: {check.incremental_updates}</small>
              </article>
            ))}
            {digital.limitations.map((limitation) => <p className="evidence-caution" key={limitation}>{limitation}</p>)}
          </EvidenceSection>
        )}
        {codes && (
          <EvidenceSection title="QR and barcode evidence" status={codes.status} summary={codes.explanation}>
            <p>Expected: {codes.expected} · Detected: {codes.detected_count} · Decoded: {codes.decoded_count} · Coverage: {Math.round(codes.coverage_score)}%</p>
            {codes.states.map((state) => <p key={state}>{codeStateLabel(state)}</p>)}
            {codes.results.map((code) => (
              <article className="evidence-record" key={code.code_index}>
                <strong>{codeStateLabel(code.state)} · page {code.page_number}</strong>
                <p>{code.explanation}</p>
                <small>Visible consistency: {code.visible_fields_consistent === undefined ? 'not established' : code.visible_fields_consistent ? 'consistent' : 'mismatch'} · Cryptographic check: {evidenceLabel(code.cryptographic_verification_result)}</small>
              </article>
            ))}
          </EvidenceSection>
        )}
        {metadata && (
          <EvidenceSection title="Metadata and provenance" status={metadata.status} summary={metadata.explanation}>
            {metadata.available_fields.length > 0 && <p>Available fields: {metadata.available_fields.join(', ')}</p>}
            {metadata.indicators.map((indicator, index) => (
              <article className="evidence-record" key={`${indicator.category}-${index}`}>
                <strong>{evidenceLabel(indicator.category)} · {evidenceLabel(indicator.status)}</strong>
                <p>{indicator.explanation}</p>
              </article>
            ))}
            {metadata.limitations.map((limitation) => <p className="evidence-caution" key={limitation}>{limitation}</p>)}
          </EvidenceSection>
        )}
        {logical && (
          <EvidenceSection title="Logical field consistency" status={logical.status} summary={logical.explanation}>
            <p>{logical.passed_count} passed · {logical.failed_count} failed · {logical.skipped_count} skipped</p>
            {logical.results.map((rule) => (
              <article className="evidence-record" key={rule.rule_id}>
                <strong>{evidenceLabel(rule.rule_id)} · {evidenceLabel(rule.status)}</strong>
                <p>{rule.explanation}</p>
                {Object.keys(rule.fields_used).length > 0 && <small>Fields: {Object.entries(rule.fields_used).map(([name, value]) => `${name}: ${value ?? 'unavailable'}`).join(' · ')}</small>}
              </article>
            ))}
          </EvidenceSection>
        )}
        {handwriting && (
          <EvidenceSection title="Handwriting similarity" status={handwriting.status} summary={handwriting.explanation}>
            <p>Writer-consistency: {handwriting.similarity_score === undefined ? 'not scored' : `${Math.round(handwriting.similarity_score)}/100`} · Coverage: {Math.round(handwriting.coverage_score)}% · Closest: {handwriting.closest_exemplar ?? 'none'}</p>
            {handwriting.reasons.map((reason) => <p key={reason}>{reason}</p>)}
            {handwriting.limitations.map((limitation) => <p className="evidence-caution" key={limitation}>{limitation}</p>)}
          </EvidenceSection>
        )}
        {signature && (
          <EvidenceSection title="Signature similarity" status={signature.status} summary={signature.explanation}>
            <p>Author-consistency: {signature.similarity_score === undefined ? 'not scored' : `${Math.round(signature.similarity_score)}/100`} · Coverage: {Math.round(signature.coverage_score)}% · Closest: {signature.closest_exemplar ?? 'none'}</p>
            {signature.compositing_score !== undefined && <p>Independent paste/compositing indicator: {Math.round(signature.compositing_score)}/100</p>}
            {signature.reasons.map((reason) => <p key={reason}>{reason}</p>)}
            {signature.limitations.map((limitation) => <p className="evidence-caution" key={limitation}>{limitation}</p>)}
          </EvidenceSection>
        )}
        {assessment && (
          <EvidenceSection title="Unified investigative assessment" status={assessment.status} summary={assessment.summary}>
            <dl className="assessment-dimensions">
              {assessment.dimensions.map((dimension) => (
                <div key={dimension.dimension}>
                  <dt>{evidenceLabel(dimension.dimension)}</dt>
                  <dd>{evidenceLabel(dimension.status)}{dimension.score === undefined ? '' : ` · ${Math.round(dimension.score)}`}</dd>
                </div>
              ))}
            </dl>
            {assessment.limitations.map((limitation) => <p className="evidence-caution" key={limitation}>{limitation}</p>)}
          </EvidenceSection>
        )}
      </div>
    </section>
  )
}

function DocuVaultReport({ result }: { result: DocumentResult }) {
  const assessment = result.reference_profile
  const profile = assessment?.selected_profile
  const alternatives = assessment?.top_matches.filter((match) => match.profile_id !== profile?.profile_id) ?? []
  const matchReasons = profile?.match_reasons.length
    ? profile.match_reasons.slice(0, 4)
    : profile?.explanation
      ? [profile.explanation]
      : []
  const checkedItems = assessment?.checked_items ?? []
  const unverifiedItems = assessment?.unverified_items ?? []
  const referenceAsset = assessment?.reference_asset
  const matchedItems = assessment?.matched_items?.length
    ? assessment.matched_items
    : matchReasons
  const differedItems = assessment?.differed_items ?? []
  const visualCoverage = assessment?.visual_comparison_coverage
    ?? profile?.visual_comparison_coverage
    ?? 0
  const referenceSourceLabel = assessment?.reference_source_label
    || referenceAsset?.source_label
    || 'Metadata and layout profile only'
  const selectedExemplarId = assessment?.selected_exemplar
    ?? profile?.selected_exemplar_id
  const selectedExemplarLabel = selectedExemplarId
    ? evidenceLabel(selectedExemplarId.replace(/[.-]+/g, '_'))
    : 'No visual exemplar selected'
  const selectedReferencePage = result.pages.find((page) => (
    Boolean(page.reference_image_url)
    && (page.reference_page_number ?? page.page_number) === (referenceAsset?.page_number ?? 1)
  )) ?? result.pages.find((page) => Boolean(page.reference_image_url))
  const referenceImageUrl = selectedReferencePage?.reference_image_url
  const referenceImageAvailable = Boolean(
    (assessment?.reference_image_available ?? profile?.visual_reference_available)
    && referenceImageUrl,
  )
  const syntheticReference = referenceAsset?.source_class === 'synthetic_demo'
    || referenceAsset?.demonstration_only
  const sourceTone = syntheticReference
    ? 'synthetic'
    : referenceSourceLabel === 'Metadata and layout profile only'
      ? 'limited'
      : 'trusted'

  return (
    <section className="docuvault-report" aria-label="DocuVault profile report">
      <header className="docuvault-profile-card">
        <div className="docuvault-profile-card__identity">
          <span className="section-kicker">Matched DocuVault profile</span>
          {profile ? (
            <>
              <h2>{profile.display_name}</h2>
              <p>{profile.issuer}</p>
              <small>
                {profile.document_category}
                {profile.version_label ? ` · ${profile.version_label}` : ''}
              </small>
            </>
          ) : (
            <>
              <h2>No suitable profile identified</h2>
              <p>The available local profiles did not provide a reliable match.</p>
            </>
          )}
        </div>
        {profile && (
          <div className="docuvault-profile-card__status">
            <span className={`profile-match-level profile-match-level--${profile.match_level.toLowerCase()}`}>
              {profile.match_level} profile match
            </span>
            <span className={`docuvault-source-badge docuvault-source-badge--${sourceTone}`}>
              {referenceSourceLabel}
            </span>
            <strong>Reference available: {profile.reference_capability || capabilityLabel(profile.capability_tier)}</strong>
            <strong>Reference image: {referenceImageAvailable ? 'Available' : 'Not available in this result'}</strong>
          </div>
        )}
        <p className="docuvault-profile-card__summary">
          {assessment?.result_summary || assessment?.explanation || 'The result is limited to the evidence supported by the selected profile.'}
        </p>
        {profile && (
          <p className="docuvault-neutral-note">
            {assessment?.closest_fallback_used ? 'This is the closest available profile. ' : ''}
            A profile match does not prove that the issuer produced this document and is separate from tampering risk.
          </p>
        )}
      </header>

      {syntheticReference && (
        <aside className="docuvault-synthetic-warning" aria-label="Synthetic reference notice">
          <AlertIcon />
          <p>This visual reference is fictional and is provided for demonstration and detector evaluation.</p>
        </aside>
      )}

      {referenceImageAvailable && referenceImageUrl && selectedReferencePage && (
        <section className="docuvault-reference-preview" aria-label="Selected visual reference">
          <a
            className="docuvault-reference-preview__thumbnail"
            href={referenceImageUrl}
            target="_blank"
            rel="noreferrer"
            aria-label="Open visual reference thumbnail"
          >
            <img
              src={referenceImageUrl}
              alt={`${profile?.display_name ?? 'Selected profile'} trusted visual reference thumbnail`}
            />
          </a>
          <div className="docuvault-reference-preview__copy">
            <span className="section-kicker">Exemplar used</span>
            <h3>{selectedExemplarLabel}</h3>
            <p>
              Page {referenceAsset?.page_number ?? selectedReferencePage.reference_page_number ?? selectedReferencePage.page_number}
              {referenceAsset && referenceAsset.page_count > 1 ? ` of ${referenceAsset.page_count}` : ''}
              {' / '}{referenceAsset?.side ?? 'single'}
            </p>
            <small>
              {referenceAsset?.issuer || profile?.issuer || 'Issuer not specified'}
              {(referenceAsset?.profile_version || profile?.version_label)
                ? ` / Version ${referenceAsset?.profile_version || profile?.version_label}`
                : ''}
            </small>
            <a href={referenceImageUrl} target="_blank" rel="noreferrer">
              View trusted visual reference <ArrowIcon />
            </a>
          </div>
          <div className="docuvault-reference-preview__coverage">
            <span>Visual-comparison coverage</span>
            <strong>{Math.round(visualCoverage)}%</strong>
            <div
              className="docuvault-coverage-track"
              role="progressbar"
              aria-label="Visual-comparison coverage"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(visualCoverage)}
            >
              <i style={{ width: `${visualCoverage}%` }} />
            </div>
          </div>
        </section>
      )}

      <div className="docuvault-report__columns">
        <section className="docuvault-report-block" aria-labelledby="docuvault-match-reasons">
          <h3 id="docuvault-match-reasons">Why this profile matched</h3>
          {matchReasons.length ? (
            <ul>{matchReasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
          ) : (
            <p>No profile-specific match reasons were available.</p>
          )}
        </section>
        <section className="docuvault-report-block" aria-labelledby="docuvault-checked-items">
          <h3 id="docuvault-checked-items">What was checked</h3>
          {checkedItems.length ? (
            <ul className="docuvault-check-list">
              {checkedItems.map((item) => <li key={item}><CheckIcon /> <span>{item}</span></li>)}
            </ul>
          ) : (
            <p>No completed profile checks were reported.</p>
          )}
        </section>
      </div>

      <div className="docuvault-report__columns">
        <section className="docuvault-report-block" aria-labelledby="docuvault-matched-items">
          <h3 id="docuvault-matched-items">What matched</h3>
          {matchedItems.length ? (
            <ul className="docuvault-check-list">
              {matchedItems.map((item) => <li key={item}><CheckIcon /> <span>{item}</span></li>)}
            </ul>
          ) : (
            <p>No supported visual match was reported.</p>
          )}
        </section>
        <section className="docuvault-report-block" aria-labelledby="docuvault-differed-items">
          <h3 id="docuvault-differed-items">What differed</h3>
          {differedItems.length ? (
            <ul>{differedItems.map((item) => <li key={item}>{item}</li>)}</ul>
          ) : (
            <p>{profile?.visual_risk_allowed
              ? 'No reportable visual difference was provided.'
              : 'Visual differences were not evaluated for this profile.'}</p>
          )}
        </section>
      </div>

      <section className={`docuvault-interpretation${profile?.visual_risk_allowed ? '' : ' docuvault-interpretation--limited'}`} aria-labelledby="docuvault-visual-interpretation">
        <div>
          <ShieldIcon />
          <h3 id="docuvault-visual-interpretation">Visual evidence interpretation</h3>
        </div>
        <p>{assessment?.visual_tampering_interpretation || 'No trusted pixel comparison was performed.'}</p>
        <small>
          Visual-comparison coverage: {Math.round(visualCoverage)}%. Limited coverage is not evidence of tampering.
        </small>
      </section>

      {unverifiedItems.length > 0 && (
        <section className="docuvault-unverified" aria-labelledby="docuvault-unverified-items">
          <div>
            <ShieldIcon />
            <h3 id="docuvault-unverified-items">Could not be verified</h3>
          </div>
          <ul>{unverifiedItems.map((item) => <li key={item}>{item}</li>)}</ul>
          <p>Unavailable checks reduce coverage; they are not suspicious findings.</p>
        </section>
      )}

      {alternatives.length > 0 && (
        <details className="docuvault-disclosure">
          <summary>Alternative profile matches ({alternatives.length})</summary>
          <ol className="docuvault-alternatives">
            {alternatives.map((match) => (
              <li key={match.profile_id}>
                <div><strong>{match.display_name}</strong><span>{match.issuer}</span></div>
                <small>{match.match_level} match · {match.reference_capability || capabilityLabel(match.capability_tier)}</small>
              </li>
            ))}
          </ol>
        </details>
      )}

      <details className="docuvault-disclosure docuvault-technical">
        <summary>Technical details</summary>
        {profile && (
          <div className="docuvault-technical__profile">
            <dl>
              <div><dt>Profile ID</dt><dd>{profile.profile_id}</dd></div>
              <div><dt>Profile score</dt><dd>{Math.round(profile.score)}/100</dd></div>
              <div><dt>Capability tier</dt><dd>{capabilityLabel(profile.capability_tier)}</dd></div>
              <div><dt>Provenance</dt><dd>{profile.provenance_assurance}</dd></div>
              <div><dt>Source class</dt><dd>{referenceAsset?.source_class ?? 'none'}</dd></div>
              <div><dt>Selected exemplar ID</dt><dd>{selectedExemplarId ?? 'none'}</dd></div>
              <div><dt>Visual alignment</dt><dd>{Math.round(profile.visual_alignment_quality ?? 0)}/100</dd></div>
              <div><dt>Risk policy</dt><dd>{profile.visual_policy_reason || 'No visual risk policy was reported.'}</dd></div>
              {referenceAsset && <div><dt>Reference asset</dt><dd>Page {referenceAsset.page_number} · {referenceAsset.side} · {referenceAsset.trust_level}</dd></div>}
            </dl>
            {Object.keys(profile.component_scores).length > 0 && (
              <dl aria-label="Profile component scores">
                {Object.entries(profile.component_scores).map(([name, score]) => (
                  <div key={name}><dt>{evidenceLabel(name)}</dt><dd>{Math.round(score)}/100</dd></div>
                ))}
              </dl>
            )}
            {Object.keys(profile.exemplar_scores ?? {}).length > 0 && (
              <dl aria-label="Exemplar scores">
                {Object.entries(profile.exemplar_scores ?? {}).map(([name, score]) => (
                  <div key={name}><dt>{name}</dt><dd>{Math.round(score)}/100</dd></div>
                ))}
              </dl>
            )}
            {referenceAsset?.source_url && (
              <a href={referenceAsset.source_url} target="_blank" rel="noreferrer">Reference provenance source</a>
            )}
          </div>
        )}
        <EvidenceTechnicalOverview result={result} hideProfile />
      </details>
    </section>
  )
}

function EvidenceOverview({ result }: { result: DocumentResult }) {
  if (result.comparison_mode === 'docuvault') return <DocuVaultReport result={result} />
  return <EvidenceTechnicalOverview result={result} />
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
  const allSuggestions = (result.region_suggestions?.length
    ? result.region_suggestions
    : pages.flatMap((page) => page.region_suggestions ?? []))
  const suggestions = allSuggestions
    .filter((suggestion) => (
      suggestion.page_number === selectedPage?.page_number
      && suggestion.role === 'variable'
    ))
  const allowedTemplateChanges = result.comparison_mode === 'template'
    ? allSuggestions.filter((suggestion) => (
      suggestion.role === 'variable'
      && !allFindings.some((finding) => (
        finding.page_number === suggestion.page_number
        && boxesOverlap(finding.bounding_box, suggestion.bounding_box)
      ))
    ))
    : []
  const anomalies = result.page_order_anomalies ?? []
  const totalPages = result.total_page_count ?? pages.length
  const referencePages = result.reference_page_count ?? result.reference?.page_count ?? pages.length
  const candidatePages = result.candidate_page_count ?? result.candidate?.page_count ?? pages.length
  const aggregate = result.document_aggregate
  const isDocuVault = result.comparison_mode === 'docuvault'
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
  const selectedProfile = result.reference_profile?.selected_profile
  const profileHasVisualReference = result.reference_profile?.reference_image_available
    ?? Boolean(selectedProfile?.visual_reference_available)
  const showReferenceViewer = !isDocuVault || Boolean(
    profileHasVisualReference && selectedPage?.reference_image_url,
  )

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

      {isDocuVault && <EvidenceOverview result={result} />}

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

      {!isDocuVault && <EvidenceOverview result={result} />}

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
        {suggestions.length > 0 && <span className="variable-region-key"><i /> {suggestions.length} detected Template value {suggestions.length === 1 ? 'change' : 'changes'}</span>}
      </div>

      <div className="result-grid">
        <div className="viewer-column">
          <div className={`document-comparison${showReferenceViewer ? '' : ' document-comparison--candidate-only'}`} aria-label="Selected page comparison">
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
            {showReferenceViewer && (
              <DocumentViewer
                imageUrl={referencePageMissing ? undefined : selectedPage?.reference_image_url}
                width={selectedPage?.width}
                height={selectedPage?.height}
                pageNumber={referencePageNumber}
                totalPages={referencePages}
                pageStatus={selectedPage?.status}
                side="reference"
                pageMissing={referencePageMissing}
                label={isDocuVault
                  ? `${selectedProfile?.display_name ?? 'Trusted profile'} reference / ${result.reference_profile?.reference_source_label || result.reference_profile?.reference_asset?.source_label || capabilityLabel(selectedProfile?.capability_tier ?? 'visual_reference')} / page ${selectedPage?.page_number ?? 1}`
                  : `Trusted reference · page ${selectedPage?.page_number ?? 1}`}
              />
            )}
          </div>
          {isDocuVault && !showReferenceViewer && (
            <aside className="docuvault-viewer-notice" role="note">
              <ShieldIcon />
              <div>
                <strong>{profileHasVisualReference
                  ? 'No trusted visual specimen is available for this page.'
                  : 'No trusted visual specimen is available for this profile.'}</strong>
                <p>{profileHasVisualReference
                  ? 'Visual comparison is unavailable on this page; the reported checks remain limited to supported evidence.'
                  : 'Checks are limited to metadata, text, layout and configured rules.'}</p>
              </div>
            </aside>
          )}
        </div>

        <aside className="findings-panel">
          <div className="findings-panel__heading">
            <h2>{isDocuVault ? 'What needs attention' : 'Findings'}</h2>
            <span>{result.finding_count} {isDocuVault ? 'requiring review' : 'suspicious'}</span>
          </div>
          <div className="findings-panel__content">
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
                <strong>{isDocuVault ? 'No evidence-backed concerns' : 'No suspicious findings'}</strong>
                <p>{isDocuVault
                  ? 'Unavailable checks are listed separately and do not raise tampering risk.'
                  : allowedTemplateChanges.length
                  ? `${allowedTemplateChanges.length} allowed Template ${allowedTemplateChanges.length === 1 ? 'change was' : 'changes were'} detected below.`
                  : 'No reportable differences found.'}</p>
              </div>
            )}
            {allowedTemplateChanges.length > 0 && (
              <section className="allowed-changes" aria-label="Allowed Template changes">
                <div className="allowed-changes__heading">
                  <div>
                    <strong>Allowed Template changes</strong>
                    <span>Detected value differences with consistent forensic integrity</span>
                  </div>
                  <span>{allowedTemplateChanges.length} detected</span>
                </div>
                <ul>
                  {allowedTemplateChanges.map((suggestion) => (
                    <li key={suggestion.suggestion_id}>
                      <i aria-hidden="true" />
                      <span>
                        <strong>{suggestion.label || 'Variable field'}</strong>
                        <small>{suggestion.reason}</small>
                      </span>
                      <em>Page {suggestion.page_number} - Allowed</em>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </div>
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
            <div><dt>Trusted reference</dt><dd>{result.comparison_mode === 'docuvault'
              ? result.reference_profile?.selected_profile
                ? `${result.reference_profile.selected_profile.display_name} · ${result.reference_profile.selected_profile.issuer} · ${result.reference_profile.selected_profile.reference_capability}`
                : 'No profile selected'
              : `${referencePages} ${referencePages === 1 ? 'page' : 'pages'}`}</dd></div>
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
  const [handwritingExemplars, setHandwritingExemplars] = useState<File[]>([])
  const [signatureExemplars, setSignatureExemplars] = useState<File[]>([])
  const [profileOverride, setProfileOverride] = useState('')
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
    setHandwritingExemplars([])
    setSignatureExemplars([])
    setProfileOverride('')
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
    if (!candidate || submitting || (comparisonMode !== 'docuvault' && !reference)) return
    setSubmitting(true)
    setError(null)
    setPagePreviews({})
    setProgress(initialProgress)
    setScreen('analysis')
    try {
      const advancedInputs: AdvancedEvidenceInputs = {
        handwritingExemplars,
        signatureExemplars,
        profileOverride: profileOverride.trim() || undefined,
      }
      const hasAdvancedInputs = handwritingExemplars.length > 0
        || signatureExemplars.length > 0
        || Boolean(profileOverride.trim())
      const created = comparisonMode === 'docuvault'
        ? hasAdvancedInputs
          ? await createAutomaticAnalysis(candidate, advancedInputs)
          : await createAutomaticAnalysis(candidate)
        : hasAdvancedInputs
          ? await createAnalysis(reference as File, candidate, comparisonMode, advancedInputs)
          : await createAnalysis(reference as File, candidate, comparisonMode)
      watchJob(created)
    } catch (requestError) {
      setError(errorFromUnknown(requestError))
      setScreen('error')
      setSubmitting(false)
    }
  }, [candidate, comparisonMode, handwritingExemplars, profileOverride, reference, signatureExemplars, submitting, watchJob])

  const startDemo = useCallback(async () => {
    if (submitting) return
    watchCleanup.current?.()
    setReference(null)
    setCandidate(null)
    setCandidatePreview(undefined)
    setComparisonMode('exact')
    setHandwritingExemplars([])
    setSignatureExemplars([])
    setProfileOverride('')
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
            handwritingExemplars={handwritingExemplars}
            signatureExemplars={signatureExemplars}
            profileOverride={profileOverride}
            onHandwritingExemplars={setHandwritingExemplars}
            onSignatureExemplars={setSignatureExemplars}
            onProfileOverride={setProfileOverride}
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
