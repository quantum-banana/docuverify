import { useRef, useState, type ChangeEvent, type DragEvent } from 'react'
import { CloseIcon, FileIcon, UploadIcon } from './Icons'

interface FileDropzoneProps {
  id: string
  eyebrow: string
  title: string
  description: string
  file: File | null
  onFile: (file: File | null) => void
  tone: 'reference' | 'candidate'
}

const formatBytes = (bytes: number): string => {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`
}

export function FileDropzone({
  id,
  eyebrow,
  title,
  description,
  file,
  onFile,
  tone,
}: FileDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  const choose = () => inputRef.current?.click()
  const handleChange = (event: ChangeEvent<HTMLInputElement>) => {
    onFile(event.target.files?.[0] ?? null)
    event.target.value = ''
  }
  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDragging(false)
    const dropped = event.dataTransfer.files?.[0]
    if (dropped) onFile(dropped)
  }

  return (
    <div
      className={`dropzone dropzone--${tone}${dragging ? ' is-dragging' : ''}${file ? ' has-file' : ''}`}
      onDragEnter={(event) => {
        event.preventDefault()
        setDragging(true)
      }}
      onDragOver={(event) => event.preventDefault()}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false)
      }}
      onDrop={handleDrop}
    >
      <input
        ref={inputRef}
        id={id}
        className="visually-hidden"
        type="file"
        accept=".pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg"
        onChange={handleChange}
        tabIndex={-1}
        data-testid={`${id}-input`}
      />
      <div className="dropzone__topline">
        <span className="dropzone__eyebrow">{eyebrow}</span>
        <span className="dropzone__index">{tone === 'reference' ? '01' : '02'}</span>
      </div>
      {file ? (
        <div className="dropzone__file">
          <span className="dropzone__icon"><FileIcon /></span>
          <span className="dropzone__file-copy">
            <strong>{file.name}</strong>
            <small>
              {formatBytes(file.size)} · {file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')
                ? 'Pages pending'
                : '1 page · Ready'}
            </small>
          </span>
          <button
            type="button"
            className="icon-button"
            aria-label={`Remove ${file.name}`}
            onClick={() => onFile(null)}
          >
            <CloseIcon />
          </button>
        </div>
      ) : (
        <button
          type="button"
          className="dropzone__empty"
          aria-label={`${eyebrow}: choose a file`}
          onClick={choose}
        >
          <span className="dropzone__icon"><UploadIcon /></span>
          <span>
            <strong>{title}</strong>
            <span className="dropzone__description">{description}</span>
          </span>
        </button>
      )}
    </div>
  )
}
