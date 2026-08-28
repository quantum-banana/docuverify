import { useEffect } from 'react'
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

  useEffect(() => {
    if (!finding) return undefined
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [finding, onClose])

  return (
    <AnimatePresence>
      {finding && (
        <>
          <motion.button
            type="button"
            className="drawer-scrim"
            aria-label="Close evidence details"
            onClick={onClose}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: reduceMotion ? 0 : 0.18 }}
          />
          <motion.aside
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
                <span className="eyebrow">Evidence {String(index + 1).padStart(2, '0')}</span>
                <h2 id="evidence-title">{finding.title}</h2>
              </div>
              <button type="button" className="icon-button" aria-label="Close evidence drawer" onClick={onClose}>
                <CloseIcon />
              </button>
            </div>

            <div className="evidence-drawer__body">
              <div className="finding-severity-line">
                <span className={`severity severity--${finding.severity.toLowerCase()}`}>
                  {finding.severity}
                </span>
                <span>{humanize(finding.category)}</span>
                {finding.region_role && finding.region_role !== 'unknown' && (
                  <span>{humanize(finding.region_role)} region</span>
                )}
                <span>Page {finding.page_number}</span>
              </div>

              <p className="evidence-explanation">{finding.explanation}</p>

              <div className="evidence-scores">
                <div><span>Tampering risk</span><strong>{Math.round(finding.risk_score)}<small>/100</small></strong></div>
                <div><span>Confidence</span><strong>{Math.round(finding.confidence_score)}<small>%</small></strong></div>
              </div>

              <section className="evidence-section">
                <div className="section-heading">
                  <span>Visual evidence</span>
                  <small>{finding.evidence_source}</small>
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

              <section className="evidence-section">
                <div className="section-heading"><span>Supporting measurements</span></div>
                <dl className="measurement-list">
                  {Object.entries(finding.measurements).length ? (
                    Object.entries(finding.measurements).map(([key, value]) => (
                      <div key={key}>
                        <dt>{humanize(key)}</dt>
                        <dd>{formatMeasurement(value)}</dd>
                      </div>
                    ))
                  ) : (
                    <div><dt>Evidence source</dt><dd>{finding.evidence_source}</dd></div>
                  )}
                  <div>
                    <dt>Normalized region</dt>
                    <dd>
                      x {finding.bounding_box.x.toFixed(3)} · y {finding.bounding_box.y.toFixed(3)} ·{' '}
                      w {finding.bounding_box.width.toFixed(3)} · h {finding.bounding_box.height.toFixed(3)}
                    </dd>
                  </div>
                </dl>
              </section>
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  )
}
