# DocuVerify

> Upload the document. See the evidence. Trust what can be explained.

DocuVerify is a local-first document comparison prototype. Phase 1 compares one trusted reference with one questioned PDF or image, localizes visual differences, and presents a bounded tampering-risk assessment with inspectable evidence. It is an investigative aid, not a legal authenticity determination.

Phase 1 passed its same-machine acceptance verification and remains intentionally uncommitted for review. See [HANDOFF.md](HANDOFF.md) and [docs/verification-status.md](docs/verification-status.md) for the exact evidence and limitations.

## Phase 1 vertical slice

- Exact reference-versus-candidate comparison for one-page PDF, PNG, JPG, and JPEG files
- Content, file-size, corruption, and single-page validation with structured errors
- Deterministic bundled reference, clean candidate, and tampered demonstration fixtures
- Backend-rendered browser-safe page previews
- ORB/RANSAC alignment with a bounded fallback for pages that lack reliable features
- Real pixel, edge, structure, and available-text comparison
- Deterministic tampering risk, assessment confidence, analysis coverage, and localized findings
- Real backend stage events over Server-Sent Events (SSE), with frontend reconnect and status-poll fallback
- Responsive SVG evidence markers and an accessible evidence drawer
- Local FastAPI job store and generated evidence assets
- Backend and frontend automated checks plus Windows bootstrap, diagnostic, run, and test scripts

## Verified checkpoint

The application started through `scripts/run-local.ps1`, diagnostics returned ready, and commit-repair verification ran the consolidated test script twice consecutively with exit code 0. Backend testing reported 34 passed tests with 189 non-failing FastAPI/Python 3.14 deprecation warnings in 10.34 and 10.16 seconds. Frontend verification reported 2 passing files and 8 passing tests in 1.94 and 1.88 seconds; both TypeScript checks passed. Both Vite 7.3.6 production builds transformed 435 modules and completed in 1.69 and 1.70 seconds; final output was HTML 0.60 kB (gzip 0.36 kB), CSS 34.58 kB (gzip 8.19 kB), and JavaScript 357.56 kB (gzip 113.62 kB).

The final post-hardening smoke sequence ran through the frontend proxy. Health returned `ok`; diagnostics reported `backend_ready=true`, `gpu_detected=true`, provider `pymupdf_embedded_text`, and CPU execution. Demo 1 produced risk 93.6, one finding, 1,990 ms pipeline time, 2.042 seconds wall time, IoU 0.2201, and all 10 stages. The clean comparison produced risk 0, no findings, 358 ms pipeline time, and 0.402 seconds wall time. Demo 2 repeated risk 93.6 and one finding in 438 ms pipeline / 0.540 seconds wall time with all 10 stages. Scores remained stable and evidence PNG delivery succeeded. Backend access-log inspection contained only expected 200/202 responses and no errors.

In-app browser QA was attempted, but no browser backend was connected. No screenshot or browser-console result is claimed. Marker/drawer interaction, progress handling, completed results, and error presentation are covered by the passing frontend tests.

## Windows prerequisites

- Windows 10 or Windows 11 x64
- Windows PowerShell 5.1 or PowerShell 7
- A runnable 64-bit Python; Python 3.11 is the preferred conservative choice
- A maintained Node.js/npm installation compatible with the checked-in frontend dependencies
- Git for repository workflows (not required merely to run an existing checkout)
- Enough free space for dependencies and temporary rendered evidence

An NVIDIA GPU is optional. The Phase 1 comparison path and embedded-PDF text fallback run on CPU. The diagnostic script reports an NVIDIA GPU when present but does not treat driver-reported CUDA compatibility as proof that GPU OCR is installed.

## Quick start

Run these commands from the repository root. The per-process execution-policy override is included because Windows may block the npm PowerShell shim; the project scripts call `npm.cmd` directly.

    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\diagnose-windows.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap-windows.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-local.ps1

Bootstrap is idempotent: it reuses a valid `.venv`, installs the declared backend and frontend dependencies, creates ignored runtime directories, and creates `.env` only when it does not already exist. It will not replace an existing environment file or an incomplete virtual environment.

Open:

- Frontend: <http://127.0.0.1:5173>
- Backend health: <http://127.0.0.1:8000/api/v1/health>
- Safe diagnostics: <http://127.0.0.1:8000/api/v1/diagnostics>
- Interactive API documentation: <http://127.0.0.1:8000/api/docs>

Press Ctrl+C in the run-script terminal to stop both development services. Paths containing spaces are supported.

## Use the application

For a manual pair, select one trusted reference and one questioned document, keep the comparison mode set to Exact, and start the analysis. For the deterministic fixture, select **Run synthetic demo**. Both paths submit a real backend job; the demo does not bypass rendering, alignment, comparison, or scoring.

Progress shown by the interface comes from actual backend stage boundaries. On completion, select a marker or finding to inspect its explanation, risk, confidence, reference crop, candidate crop, difference overlay, and supporting measurements.

Raw PDF uploads are never passed to a browser PDF parser or embedded as a local object URL. The frontend shows a neutral placeholder while analysis is pending, then displays only the backend-rendered, browser-safe PNG preview. An automated regression test enforces this boundary.

## Tests

The complete Windows check runs all mandatory steps and returns a failing exit code if any one fails:

    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-tests.ps1

That command runs, in order:

1. Backend pytest suite
2. Frontend test suite
3. TypeScript typecheck
4. Frontend production build

Each test-script invocation creates a unique `%TEMP%\docuverify-tests\run-<process-id>-<random-guid>` root owned by the current Windows user, with separate pytest and application-runtime children. This prevents collisions across processes and Windows execution identities, does not require the legacy ignored `backend/runtime/pytest-temp` directory, and needs no administrator access. Production continues to use its configured ignored local runtime directory.

Exact observed results are recorded in [HANDOFF.md](HANDOFF.md) and [docs/verification-status.md](docs/verification-status.md).

## Architecture

The React/TypeScript frontend owns file selection, stage/result presentation, SSE recovery, normalized marker rendering, and evidence interaction. The FastAPI backend owns validation, private file storage, job state, rendering, text extraction, alignment, difference localization, scoring, and safe asset delivery. Generated inputs and evidence stay beneath the ignored runtime area.

The backend Pydantic contract is canonical for API responses; the frontend keeps corresponding TypeScript contracts and conversion at its API boundary. Finding coordinates use normalized candidate-page values in the range 0 to 1 so an SVG overlay tracks the preview at any display size.

See [docs/architecture.md](docs/architecture.md) for the lifecycle, storage boundary, SSE behavior, coordinate mapping, OCR abstraction, and multi-page extension seam.

## Privacy and retention

- Analysis is local; the application does not upload documents to a cloud service.
- Original uploads, SQLite job state, rendered pages, and evidence crops are written only to the configured ignored runtime directory.
- Filenames are sanitized and files are stored under generated internal names.
- Asset routes resolve registered job assets; they do not accept arbitrary filesystem paths.
- Retention and periodic cleanup are configurable with `DOCUVERIFY_RETENTION_HOURS` and `DOCUVERIFY_CLEANUP_INTERVAL_SECONDS` (defaults: 24 hours and 300 seconds).
- Cleanup considers only terminal completed/failed jobs. It removes the job directory first and deletes SQLite state only after filesystem cleanup succeeds; a Windows file lock leaves the record intact for a later retry.
- Interrupted queued/running jobs are recovered when the FastAPI lifespan starts. Importing or constructing the app does not mutate their state.
- `.env`, runtime databases, uploads, generated evidence, logs, caches, model weights, virtual environments, and `node_modules` are excluded by `.gitignore`.

Use fictional or authorized documents during development. Deleting the ignored runtime directory removes local jobs and evidence after the services are stopped; verify the exact target before doing so.

## OCR and text fallback

Phase 1 prioritizes reliability over forcing GPU OCR. Born-digital PDFs use PyMuPDF embedded-text extraction when text is present. Raster documents and PDFs without embedded text remain comparable through the visual pipeline even when no optional OCR engine is installed. Results state the extraction source; the application must not claim OCR ran when it did not.

The safe default is `DOCUVERIFY_OCR_PROVIDER=auto` and `DOCUVERIFY_OCR_DEVICE=cpu`. Verified diagnostics reported the active provider as `pymupdf_embedded_text`, device `cpu`, and raster OCR capability `false`. The current machine preflight found no Tesseract executable or optional raster-OCR module. See [docs/environment-preflight.md](docs/environment-preflight.md).

## Current limitations

- Exactly one page per input; multi-page documents are rejected instead of truncated
- Exact trusted-reference comparison only
- No handwriting or signature enrollment
- No DocuVault or internet collection
- No user accounts, payments, blockchain, distributed worker, cloud deployment, or model training
- Raster OCR depends on an optional provider; visual comparison remains available without it
- Findings communicate tampering risk and supporting evidence, not legal authenticity or authorship
- Simultaneous backend instances must not share one runtime directory; Phase 1 has no cross-process job-store coordination
- No screenshot or manual browser-console QA was captured because the in-app browser backend was unavailable; automated UI tests passed
- RTX 5060 compatibility has not been smoke-tested

## Troubleshooting

**PowerShell says script execution is disabled.** Use the documented `powershell -NoProfile -ExecutionPolicy Bypass -File ...` form. This changes policy only for that process. The scripts invoke `npm.cmd`, not `npm.ps1`.

**`py --version` reports no installed Python.** The Windows launcher can be unregistered even when a usable interpreter exists. The shared script probes candidates by actually running them and also understands the current per-user Python install-manager layout. If bootstrap still cannot find Python, install Python 3.11 x64 and rerun diagnostics; do not hard-code a personal path in repository files.

**Bootstrap finds an incomplete `.venv`.** Move that directory aside manually after confirming it is the project-local environment, then rerun bootstrap. The script deliberately does not overwrite ambiguous local state.

**The frontend cannot reach the API.** Confirm both URLs printed by `run-local.ps1`, inspect `/api/v1/health`, and ensure another process is not using ports 5173 or 8000. The Vite development server proxies `/api` to the local backend.

**A PDF has no extracted text.** This is expected for scans without an optional OCR provider. Inspect the backend diagnostics response; visual comparison should still run.

**The marker looks noisy.** Confirm that the pair has matching page geometry and represents the same exact document revision. Phase 1 alignment is deliberately bounded and is not a general template-matching system.

## Future-phase boundary

Phase 2 can extend the existing page/result contracts to multiple pages, add per-page scheduling and event fields, and introduce stronger OCR providers without changing Phase 1's local privacy and explainability principles. DocuVault, handwriting/signature enrollment, large-model inference, blockchain, deployment, identity, and billing remain outside Phase 1.
