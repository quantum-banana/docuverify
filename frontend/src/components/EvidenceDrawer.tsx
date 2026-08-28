import { useEffect, useRef } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import type { Finding } from '../types/contracts'
import { CloseIcon, EyeIcon } from './Icons'

interface EvidenceDrawerProps {
  finding: Finding | null
  index: number
  onClose: () => void
  candidateEvidenceAvailable?: boolean
  referenceEvidenceAvailable?: boolean
}

const humanize = (value: string): string =>
  value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())

const formatMeasurement = (value: string | number | boolean | null): string => {
  if (value === null) return 'Not available'
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(3)
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  return value
}

function EvidenceImage({ src, label, unavailableLabel = 'Artifact unavailable' }: {
  src: string
  label: string
  unavailableLabel?: string
}) {
  return (
    <figure className="evidence-image">
      <figcaption>{label}</figcaption>
      {src ? (
        <img src={src} alt={`${label} for selected finding`} />
      ) : (
        <div className="evidence-image__empty"><EyeIcon /> {unavailableLabel}</div>
      )}
    </figure>
  )
}

export function EvidenceDrawer({
  finding,
  index,
  onClose,
  candidateEvidenceAvailable = true,
  referenceEvidenceAvailable = true,
}: EvidenceDrawerProps) {
  const reduceMotion = useReducedMotion()
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const drawerRef = useRef<HTMLElement>(null)
  const returnFocusRef = useRef<HTMLElement | null>(null)
  const drawerOpenRef = useRef(false)
  const onCloseRef = useRef(onClose)

  useEffect(() => {
    onCloseRef.current = onClose
  }, [onClose])

  const restoreFocus = () => {
    const target = returnFocusRef.current
    returnFocusRef.current = null
    drawerOpenRef.current = false
    if (target?.isConnected) target.focus()
  }

  useEffect(() => {
    if (!finding) {
      if (drawerOpenRef.current) restoreFocus()
      return undefined
    }

    if (!drawerOpenRef.current) {
      returnFocusRef.current = document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null
      drawerOpenRef.current = true
    }
    closeButtonRef.current?.focus()

    const manageDialogKeyboard = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        onCloseRef.current()
        return
      }
      if (event.key !== 'Tab') return
      const focusable = Array.from(drawerRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])',
      ) ?? []).filter((element) => !element.hasAttribute('hidden'))
      if (!focusable.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    window.addEventListener('keydown', manageDialogKeyboard)
    return () => window.removeEventListener('keydown', manageDialogKeyboard)
  }, [finding])

  useEffect(() => () => {
    if (drawerOpenRef.current) restoreFocus()
  }, [])

  return (
    <AnimatePresence onExitComplete={restoreFocus}>
      {finding && (
        <>
          <motion.button
            type="button"
            className="drawer-scrim"
            aria-label="Close evidence"
            onClick={onClose}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: reduceMotion ? 0 : 0.18 }}
          />
          <motion.aside
            ref={drawerRef}
            className="evidence-drawer"
            role="dialog"
            aria-modal="true"
            aria-labelledby="evidence-title"
            initial={reduceMotion ? false : { x: '100%' }}
            animate={{ x: 0 }}
            exit={reduceMotion ? { opacity: 0 } : { x: '100%' }}
            transition={{ duration: reduceMotion ? 0 : 0.3, ease: [0.22, 1, 0.36, 1] }}
          >
            <div className="evidence-drawer__header">
              <div>
                <span className="eyebrow">Finding {String(index + 1).padStart(2, '0')}</span>
                <h2 id="evidence-title">{finding.title}</h2>
              </div>
              <button
                ref={closeButtonRef}
                type="button"
                className="icon-button"
                aria-label="Close evidence"
                onClick={onClose}
              >
                <CloseIcon />
              </button>
            </div>

            <div className="evidence-drawer__body">
              <div className="finding-severity-line">
                <span className={`severity severity--${finding.severity.toLowerCase()}`}>
                  {finding.severity}
                </span>
                <span>Page {finding.page_number}</span>
              </div>

              <p className="evidence-explanation">{finding.explanation}</p>

              <div className="evidence-scores">
                <div><span>Risk</span><strong>{Math.round(finding.risk_score)}<small>/100</small></strong></div>
                <div><span>Confidence</span><strong>{Math.round(finding.confidence_score)}<small>%</small></strong></div>
              </div>

              <section className="evidence-section">
                <div className="section-heading">
                  <span>Visual evidence</span>
                </div>
                <div className="evidence-images">
                  <EvidenceImage
                    src={candidateEvidenceAvailable ? finding.candidate_crop_url : ''}
                    label="Questioned"
                    unavailableLabel={candidateEvidenceAvailable ? undefined : 'Candidate page missing'}
                  />
                  <EvidenceImage
                    src={referenceEvidenceAvailable ? finding.reference_crop_url : ''}
                    label="Trusted reference"
                    unavailableLabel={referenceEvidenceAvailable ? undefined : 'Reference page missing'}
                  />
                  <EvidenceImage src={finding.difference_overlay_url} label="Difference overlay" />
                </div>
              </section>

              <details className="analysis-details evidence-details">
                <summary>Analysis details</summary>
                <dl className="measurement-list">
                  <div><dt>Category</dt><dd>{humanize(finding.category)}</dd></div>
                  {finding.region_role && finding.region_role !== 'unknown' && (
                    <div><dt>Region role</dt><dd>{humanize(finding.region_role)}</dd></div>
                  )}
                  <div><dt>Evidence source</dt><dd>{finding.evidence_source}</dd></div>
                  {Object.entries(finding.measurements).length ? (
                    Object.entries(finding.measurements).map(([key, value]) => (
                      <div key={key}>
                        <dt>{humanize(key)}</dt>
                        <dd>{formatMeasurement(value)}</dd>
                      </div>
                    ))
                  ) : null}
                  <div>
                    <dt>Normalized region</dt>
                    <dd>
                      x {finding.bounding_box.x.toFixed(3)} · y {finding.bounding_box.y.toFixed(3)} ·{' '}
                      w {finding.bounding_box.width.toFixed(3)} · h {finding.bounding_box.height.toFixed(3)}
                    </dd>
                  </div>
                </dl>
              </details>
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  )
}
