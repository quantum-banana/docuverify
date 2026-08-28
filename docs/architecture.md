# Phase 1 architecture

## Boundary and trust model

DocuVerify is a two-process local application. The React/TypeScript/Vite frontend is an untrusted presentation client: it selects files, submits multipart requests, renders progress and results, and asks for assets only through API URLs. It never receives or constructs a local filesystem path. A selected PDF is not passed to a browser PDF parser and is not rendered through a local object URL; the preview remains a placeholder until the backend returns a browser-safe PNG. Automated frontend regression coverage enforces this rule.

FastAPI is the authority for input validation, job state, rendering, forensic analysis, scoring, and asset access. It validates declared and actual file content, enforces the one-page limit, generates internal names, computes hashes, and returns structured errors. The backend must remain the source of truth for stage completion and final scores.

## Main components

| Area | Responsibility |
| --- | --- |
| `frontend/src/api` | Versioned API calls, SSE connection, reconnect/backoff, status polling, and wire-shape conversion |
| `frontend/src/types` | UI-facing TypeScript contracts corresponding to the backend contract |
| `frontend/src/components` | Upload, document preview, normalized SVG marker, and evidence drawer behavior |
| `backend/app/models` | Canonical Pydantic request/result, progress, diagnostics, error, and finding contracts |
| `backend/app/api` | Health, diagnostics, upload/demo, job, event, and registered-asset endpoints |
| `backend/app/core` | Settings and private runtime/job storage |
| `backend/app/services` | Document validation/rendering, job orchestration, and pipeline stage boundaries |
| `backend/app/forensics` | Text comparison, alignment, localized differences, and deterministic scoring |
| `samples/synthetic` | Deterministically generated fictional demonstration inputs |
| `samples/expected` | Expected altered-region manifest/mask used by localization tests |

Paths in this document describe the intended checked-in Phase 1 layout. Test status, rather than file presence, determines whether a component is accepted.

## Job lifecycle

1. `POST /api/v1/analyses/reference` validates multipart fields and returns HTTP 202 with job, status, and event URLs. `POST /api/v1/demo/reference` loads the bundled fixture and calls the same job/pipeline path.
2. The backend creates a generated job identifier and private job directory, stores sanitized inputs under generated names, computes SHA-256 values, and persists the initial job state.
3. A bounded in-process executor advances real stages: validation, rendering, normalization, alignment, text extraction, structure comparison, difference localization, evidence scoring, result preparation, and completion.
4. Each boundary updates persistent state before appending a progress event. There are no artificial backend sleeps.
5. On success, the result and safe asset registry are persisted before the completion event. On failure, a structured public error is stored while detailed diagnostics remain in local logs.
6. The final result remains available through the status endpoint after the event stream closes.

Phase 1 uses a lightweight local executor and SQLite-backed state. Redis, Celery, external message brokers, distributed workers, and cloud job services are intentionally absent.

## SSE delivery and recovery

`GET /api/v1/analyses/{job_id}/events` streams persisted job events. Event identifiers allow a reconnecting client to request events after its last observation. A client arriving after analysis started receives the durable event history/state rather than relying on timing.

The frontend treats SSE as a notification channel, not the sole record. It reconnects with bounded backoff, polls job status while the stream is unavailable, handles very fast completion, and fetches the status/result after a terminal event when necessary. It does not visually declare a backend stage complete before the corresponding server event.

## Finding coordinate system

Each finding bounding box is measured on the normalized candidate page and serialized as `x`, `y`, `width`, and `height` fractions in the closed range 0 to 1. The top-left page corner is `(0, 0)` and the bottom-right is `(1, 1)`. Width and height must be positive, and `x + width` / `y + height` must remain within the page.

The frontend places the candidate preview and SVG overlay in the same aspect-ratio box, then applies those fractions through the SVG view box. Resizing changes displayed pixels, not evidence coordinates. Alignment transforms are retained separately; evidence returned to the UI is always mapped into candidate-page coordinates.

## Temporary and persistent local storage

The configurable runtime root defaults to `backend/runtime`. It contains the local SQLite job store and generated per-job input/evidence directories. Inputs use generated role-based names; browser assets are registered by `(job_id, asset_id)` and served through a safe lookup. An asset request cannot nominate an arbitrary relative or absolute path.

The Windows test runner creates a unique current-user-owned root beneath `%TEMP%\docuverify-tests` for every invocation. A process ID plus random GUID prevents reuse, and separate `pytest` and `runtime` children isolate pytest cleanup from application storage. The runner sets `DOCUVERIFY_RUNTIME_DIR` before backend imports, validates every path against the Windows temporary boundary, and attempts cleanup only for the exact matching per-run directory. A cleanup lock produces a warning without changing test results; the next invocation always uses a different root. The inaccessible legacy `backend/runtime/pytest-temp` directory is not required, no administrator access is needed, and production runtime behavior is unchanged.

The runtime root, uploads, databases, logs, screenshots, model caches, and local analysis artifacts are ignored by Git. No automatic cloud upload is part of Phase 1.

Retention is controlled by `DOCUVERIFY_RETENTION_HOURS` and `DOCUVERIFY_CLEANUP_INTERVAL_SECONDS` (24 hours and 300 seconds by default). FastAPI lifespan startup first invokes interrupted-job recovery, then performs an immediate retention sweep and starts the periodic cleanup task. Importing the module or constructing an application/store initializes storage but deliberately does not recover queued/running jobs; this prevents schema tooling, tests that only inspect an app, or another importing process from mutating jobs. Recovery occurs only when the ASGI lifespan actually starts.

Each retention sweep selects only terminal `completed` or `failed` jobs older than the cutoff. It validates the job path and removes filesystem data first. Only after the directory is absent does it delete the SQLite job row, which cascades to events and assets. If Windows keeps a file locked or removal is partial, cleanup retains the database record and retries on a later sweep. Queued and running jobs are never deleted by retention cleanup.

## OCR/text provider seam

Text extraction returns both content and a truthful source label. The Phase 1 baseline uses PyMuPDF embedded text for born-digital PDFs. Optional raster OCR providers can implement the same interface later; absence or failure of a provider yields a visual-only comparison rather than fabricated OCR output.

`DOCUVERIFY_OCR_PROVIDER=auto` and `DOCUVERIFY_OCR_DEVICE=cpu` are conservative defaults. GPU detection and driver-reported CUDA compatibility are diagnostics only. A future provider must verify its own runtime before reporting GPU execution.

## Contract ownership

The backend Pydantic models are the canonical wire contract and reject unexpected fields where appropriate. Frontend TypeScript types correspond to that contract, while parsing/conversion stays at the API boundary. This prevents presentation components from inventing alternate shapes and provides one place to adapt a versioned schema.

Contract-sensitive tests should cover normalized bounds, score ranges, required URLs, stable job/result fields, and representative backend responses consumed by the frontend.

## Phase 2 multi-page extension

The single-page rejection is explicit so Phase 2 can extend behavior without silently changing Phase 1 results. Multi-page support should:

1. Validate and render every page under configured count/size limits.
2. Add page identity and aggregate progress to the job scheduler and SSE contract.
3. Preserve one transform and normalized finding coordinate space per candidate page.
4. Compare page order/structure before page-local alignment.
5. Store and serve assets by job, page, and finding identifiers.
6. Aggregate page risks into a documented document score while retaining page evidence.
7. Add cancellation, resource limits, and cleanup behavior appropriate to longer jobs.

That extension can retain the frontend/backend boundary, provider abstraction, asset registry, and normalized coordinate model; it must not make Phase 1 silently analyze only the first page.
