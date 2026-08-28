# DocuVerify handoff

## Current phase and status

**Phase:** Phase 1 - four-hour demonstrable vertical slice<br>
**Status:** **PASS**<br>
**Architectural review:** **PASSED**<br>
**Base/parent commit:** NONE - the approved Phase 1 commit is the repository's first commit<br>
**Release state:** Approved for the first commit and planned annotated tag `phase-1-4h-demo`; GitHub remote setup remains pending.

Phase 1 passed its same-machine automated and isolated live verification. The application started through the Windows run script, required API capabilities responded, the complete test script exited 0, the clean result was substantially below the tampered result, and two consecutive demo runs were stable. The browser automation backend was unavailable, so screenshots and manual browser-console inspection are explicitly not claimed; UI behavior is covered by the passing frontend tests.

## Verified implementation

- Local Git repository on `main`, approved for its first Phase 1 commit
- Idempotent Windows diagnostics, bootstrap, local-run, and consolidated-test scripts
- FastAPI/Pydantic API for health, safe diagnostics, validated uploads, bundled demo, durable jobs/events, and registered assets
- One-page PDF/image rendering, bounded alignment, embedded-PDF text extraction, visual difference localization, deterministic scoring, and evidence PNGs
- Real 10-stage backend progress lifecycle delivered through replayable SSE
- React/TypeScript/Vite interface for upload/demo, progress, risk results, normalized SVG markers, and evidence details
- Deterministic fictional reference, clean candidate, tampered candidate, expected region, and tamper mask
- Shared schemas and corresponding backend/frontend contracts
- Ignored local SQLite/runtime storage with configurable periodic retention cleanup and restart recovery

## Machine and environment

- Windows 11 Home Single Language, x64, build 10.0.26200
- AMD Ryzen 9 8945HS w/ Radeon 780M Graphics, 16 logical CPUs
- 15.23 GiB observed RAM; nominal/supplied capacity is 16 GB
- Drive E: 100 GiB total with approximately 77.9 GiB free at final verification
- NVIDIA GeForce RTX 4060 Laptop GPU, 8188 MiB
- NVIDIA driver 610.62; driver-reported CUDA compatibility 13.3
- Python 3.14.6
- Node.js 24.15.0; npm 11.12.1 through `npm.cmd`
- Git 2.54.0.windows.1
- GitHub CLI unavailable; remote creation and authentication were not attempted

The driver CUDA value is not evidence that a CUDA toolkit or GPU-enabled OCR library is installed. An **RTX 5060 smoke test is still required** on separate hardware.

## Active OCR provider and device

- Provider: `pymupdf_embedded_text`
- Execution device: `cpu`
- Raster OCR capability: `false`
- GPU detected by backend diagnostics: `true`
- Raster/no-text fallback: real visual comparison without claiming OCR

## Commands

From the repository root:

    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\diagnose-windows.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap-windows.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-local.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-tests.ps1

Local URLs default to frontend `http://127.0.0.1:5173`, backend `http://127.0.0.1:8000`, health `/api/v1/health`, diagnostics `/api/v1/diagnostics`, and API docs `/api/docs`.

## Exact automated results

| Check | Result |
| --- | --- |
| Windows diagnostics | PASS, exit 0 |
| Consolidated test script | PASS twice consecutively, exit 0 on both runs |
| Backend tests | 34 passed in 10.34 seconds; 34 passed in 10.16 seconds |
| Backend warnings | 189 FastAPI/Python 3.14 deprecation warnings; non-failing |
| Frontend tests | 2 files/8 tests passed in 1.94 seconds; repeated in 1.88 seconds |
| TypeScript typecheck | PASS on both runs |
| Vite production build | PASS twice with Vite 7.3.6; 435 modules transformed; built in 1.69 and 1.70 seconds |
| Production output | HTML 0.60 kB (gzip 0.36 kB), CSS 34.58 kB (gzip 8.19 kB), JavaScript 357.56 kB (gzip 113.62 kB) |
| PDF preview security regression | PASS; raw PDFs never enter a browser parser, and the placeholder remains until a backend PNG is available |
| PowerShell syntax parse | PASS for all scripts |

Each consolidated test invocation uses a fresh current-user-owned root beneath `%TEMP%\docuverify-tests`, with separate pytest and application-runtime children named by process ID and a random GUID. This avoids collisions across Windows execution identities and concurrent runs, does not depend on the inaccessible legacy `backend/runtime/pytest-temp` directory, and requires no administrator access. Production runtime behavior is unchanged.

## Retention and restart behavior

- `DOCUVERIFY_RETENTION_HOURS` controls job age and `DOCUVERIFY_CLEANUP_INTERVAL_SECONDS` controls the periodic sweep (defaults: 24 hours and 300 seconds).
- Lifespan startup performs interrupted-job recovery and an immediate retention sweep, then starts periodic cleanup.
- Importing the module or constructing an app/store initializes required storage but does not recover queued/running jobs; recovery is deliberately deferred to lifespan startup so schema tools and app inspection cannot mutate live job state.
- Cleanup selects only terminal completed/failed jobs older than the cutoff. Queued/running jobs are never retention-deleted.
- Cleanup is filesystem-first. The SQLite job/event/asset rows are removed only after the job directory is gone.
- A locked or partially removable Windows directory leaves its database record intact, allowing the next periodic sweep to retry safely.

## Exact isolated live smoke results

| Run | Tampering risk | Findings | Pipeline time | Wall time | Localization | Events |
| --- | ---: | ---: | ---: | ---: | --- | ---: |
| Tampered demo 1 | 93.6 | 1 | 1,990 ms | 2.042 s | Expected region IoU 0.2201 | 10 stages |
| Clean comparison | 0 | 0 | 358 ms | 0.402 s | No suspicious region | Completed |
| Tampered demo 2 | 93.6 | 1 | 438 ms | 0.540 s | Stable finding | 10 stages |

This final-tree smoke ran through the frontend proxy. The health endpoint returned `ok`. Diagnostics returned backend ready with GPU detected, `pymupdf_embedded_text`, CPU execution, and raster OCR false. The tampered pipeline produced a browser-safe evidence PNG. Clean risk was substantially lower than tampered risk, and the repeated tampered risk/finding count were stable. Backend access-log inspection showed only expected 200/202 responses and no errors.

Peak RAM and peak VRAM were not measured.

## UI/browser verification boundary

- Marker interaction, evidence drawer details, backend-progress updates, completed results, and visible error behavior passed automated frontend tests.
- PDF uploads remain behind a placeholder until the backend returns a safe PNG; the frontend does not create a PDF object URL or invoke a browser PDF parser. This behavior has an automated regression test.
- The complete application started through `run-local.ps1`.
- An in-app browser session was attempted, but no browser backend was connected.
- No screenshot was captured.
- No manual browser-console inspection result is claimed.

The missing browser backend is a tooling limitation, not a product failure for this checkpoint because the relevant UI behaviors have automated coverage and the live backend pipeline was independently exercised.

## Major decisions

- The product reports **Tampering risk**, **Assessment confidence**, and **Analysis coverage**. It does not claim a percentage is fake, that a document is definitely forged, or that it is legally authentic.
- Multi-page files are rejected explicitly; Phase 1 never silently analyzes only the first page.
- The synthetic demo traverses the same job and forensic pipeline as uploaded pairs.
- Backend events are the authority for stage completion. The frontend reconnects and polls but does not invent progress.
- Findings use normalized candidate-page coordinates so SVG evidence remains aligned while resizing.
- Runtime files are local and ignored. Assets use job/asset identifiers rather than arbitrary paths.
- GPU OCR was not forced. Embedded PDF text and visual comparison provide a reliable CPU path.
- PowerShell invokes `npm.cmd` to avoid the blocked npm PowerShell shim.
- Python discovery executes candidates and supports the current per-user install-manager layout without recording a personal path.

## Known limitations

- One page and exact trusted-reference comparison only
- No raster OCR provider; visual comparison remains active
- Simultaneous backend instances sharing one runtime directory are unsupported because Phase 1 has no cross-process job-store coordination
- In-process jobs are intended for a local checkpoint, not distributed load
- Retention cleanup is local, terminal-job-only, and retry-based; it is not a user-facing records manager
- No DocuVault, external collection, handwriting/signature enrollment, blockchain, accounts, payments, deployment, or custom model training
- Scores are evidence-guided risk, not identity, provenance, or legal authentication
- Python 3.14 currently produces non-failing FastAPI deprecation warnings
- Python 3.14 compatibility with future OCR libraries must be resolved before raster/GPU OCR integration in Phase 2
- Current golden-fixture localization IoU is 0.2201 and should be improved in Phase 2
- No screenshot/manual browser-console artifact was available
- RTX 5060 compatibility remains untested

## Security and repository state

- `.env`, virtual environments, `node_modules`, runtime databases, uploaded inputs, evidence, logs, caches, model weights, and local analysis artifacts are ignored.
- Synthetic fixtures are fictional and intentional repository inputs.
- Targeted secret-pattern inspection found no committed credential material.
- Phase 1 passed architectural review and is approved for the repository's first commit and annotated tag `phase-1-4h-demo`.
- GitHub remote setup and pushing remain pending because `gh` is unavailable; no deployment or Phase 2 work is included.

## Recommended next action

**READY FOR REMOTE SETUP AFTER COMMIT AND TAG.** The approved root commit and `phase-1-4h-demo` tag capture Phase 1. A later remote-setup instruction may configure and push them. Phase 2 can then address multi-page contracts, improved localization, per-page scheduling/navigation, and pluggable raster/GPU OCR after Python compatibility is confirmed.
