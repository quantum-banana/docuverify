import { useEffect, useState, type KeyboardEvent } from 'react'
import type { Finding } from '../types/contracts'
import { EyeIcon, FileIcon, ScanIcon } from './Icons'

interface DocumentViewerProps {
  imageUrl?: string
  width?: number
  height?: number
  findings?: Finding[]
  selectedFindingId?: string
  onSelectFinding?: (finding: Finding) => void
  scanning?: boolean
  progress?: number
  label?: string
}

const activateOnKeyboard = (
  event: KeyboardEvent<SVGGElement>,
  finding: Finding,
  onSelect?: (finding: Finding) => void,
) => {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    onSelect?.(finding)
  }
}

export function DocumentViewer({
  imageUrl,
  width,
  height,
  findings = [],
  selectedFindingId,
  onSelectFinding,
  scanning = false,
  progress = 0,
  label = 'Questioned document',
}: DocumentViewerProps) {
  const [imageFailed, setImageFailed] = useState(false)
  useEffect(() => setImageFailed(false), [imageUrl])

  const pageRatio = width && height ? width / height : 0.707
  const aspectRatio = width && height ? `${width} / ${height}` : '0.707 / 1'
  // Cap width from the page ratio as well as the layout width so a tall page
  // never gets height-clamped independently of its overlay coordinate space.
  const pageMaxWidth = Math.min(510, 650 * pageRatio)
  const showDocument = Boolean(imageUrl) && !imageFailed

  return (
    <section className="document-shell" aria-label={`${label} viewer`}>
      <div className="document-shell__bar">
        <div>
          <span className="document-shell__dot" />
          <span>{label}</span>
        </div>
        <span className="document-shell__page">PAGE 01 / 01</span>
      </div>
      <div className="document-stage">
        <div className="document-stage__grid" aria-hidden="true" />
        <div className="document-page" style={{ aspectRatio, maxWidth: `${pageMaxWidth}px` }}>
          {showDocument ? (
            <img
              className="document-page__media"
              src={imageUrl}
              alt={label}
              onError={() => setImageFailed(true)}
            />
          ) : (
            <div className="document-placeholder" data-testid="document-placeholder">
              {scanning ? <ScanIcon className="document-placeholder__scan" /> : <FileIcon />}
              <strong>{scanning ? 'Preparing document preview' : 'Document preview unavailable'}</strong>
              <span>
                {scanning
                  ? 'The rendered candidate page will appear when it is ready.'
                  : 'The analysis completed without a browser preview.'}
              </span>
            </div>
          )}

          {scanning && (
            <div className="scan-overlay" aria-hidden="true">
              <div className="scan-overlay__wash" />
              <div className="scan-overlay__line" style={{ top: `${Math.max(5, Math.min(95, progress))}%` }}>
                <span />
              </div>
              <span className="scan-overlay__corner scan-overlay__corner--tl" />
              <span className="scan-overlay__corner scan-overlay__corner--tr" />
              <span className="scan-overlay__corner scan-overlay__corner--bl" />
              <span className="scan-overlay__corner scan-overlay__corner--br" />
            </div>
          )}

          {!scanning && findings.length > 0 && (
            <svg
              className="finding-overlay"
              viewBox="0 0 1 1"
              preserveAspectRatio="none"
              aria-label={`${findings.length} evidence ${findings.length === 1 ? 'marker' : 'markers'}`}
            >
              {findings.map((finding, index) => {
                const { x, y, width: boxWidth, height: boxHeight } = finding.bounding_box
                const selected = selectedFindingId === finding.finding_id
                return (
                  <g
                    key={finding.finding_id}
                    className={`finding-marker${selected ? ' is-selected' : ''}`}
                    role="button"
                    tabIndex={0}
                    aria-label={`Open evidence ${index + 1}: ${finding.title}`}
                    onClick={() => onSelectFinding?.(finding)}
                    onKeyDown={(event) => activateOnKeyboard(event, finding, onSelectFinding)}
                  >
                    <rect className="finding-marker__halo" x={x} y={y} width={boxWidth} height={boxHeight} rx="0.006" />
                    <rect className="finding-marker__box" x={x} y={y} width={boxWidth} height={boxHeight} rx="0.004" />
                    <circle className="finding-marker__pin" cx={x + boxWidth} cy={y} r="0.022" />
                    <text
                      className="finding-marker__number"
                      x={x + boxWidth}
                      y={y + 0.006}
                      textAnchor="middle"
                      dominantBaseline="middle"
                    >
                      {index + 1}
                    </text>
                  </g>
                )
              })}
            </svg>
          )}
        </div>
      </div>
      <div className="document-shell__footer">
        <span><EyeIcon /> Candidate view</span>
        <span>Normalized coordinates · 0—1</span>
      </div>
    </section>
  )
}
