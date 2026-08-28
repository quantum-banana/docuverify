# Phase 1 verification status

**Overall status: PASS**

Phase 1 passed the same-machine acceptance gate and architectural review on 2026-08-28. It is approved for the repository's first commit and planned annotated tag `phase-1-4h-demo`. GitHub remote setup remains pending.

## Host and toolchain

| Check | Status | Exact observation |
| --- | --- | --- |
| Windows diagnostics | PASS | `scripts/diagnose-windows.ps1` exited 0 |
| Windows | PASS | Windows 11 Home Single Language 10.0.26200, x64 |
| CPU/RAM | PASS | Ryzen 9 8945HS, 16 logical CPUs, 15.23 GiB observed RAM |
| Workspace disk | PASS | Drive E: 100 GiB total, approximately 77.9 GiB free |
| GPU | PASS | RTX 4060 Laptop GPU, 8188 MiB, driver 610.62, driver CUDA compatibility 13.3 |
| Python | PASS | 3.14.6 |
| Node/npm | PASS | Node 24.15.0, npm 11.12.1 through `npm.cmd` |
| Git | PASS | 2.54.0.windows.1 |
| GitHub CLI | OPTIONAL UNAVAILABLE | `gh` is absent; remote creation/authentication were not attempted |
| OCR | PASS WITH FALLBACK | `pymupdf_embedded_text`, CPU, raster OCR false |

## Automated verification

Two consecutive definitive `scripts/run-tests.ps1` invocations exited 0 using different per-run temporary roots.

| Check | Status | Exact observation |
| --- | --- | --- |
| Backend dependency readiness | PASS | Mandatory imports available; pytest executed in the project virtual environment |
| Frontend dependency readiness | PASS | Tests, typecheck, and production build executed successfully |
| Backend tests | PASS | 34 passed in 10.34 seconds; repeated with 34 passed in 10.16 seconds |
| Backend warnings | NON-BLOCKING | 189 FastAPI/Python 3.14 deprecation warnings |
| Frontend tests | PASS | 2 files/8 tests passed in 1.94 seconds; repeated in 1.88 seconds |
| TypeScript typecheck | PASS | Command completed successfully on both runs |
| Production frontend build | PASS | Vite 7.3.6 transformed 435 modules; builds completed in 1.69 and 1.70 seconds |
| Production artifacts | PASS | HTML 0.60 kB (gzip 0.36 kB), CSS 34.58 kB (gzip 8.19 kB), JavaScript 357.56 kB (gzip 113.62 kB) |
| PDF preview security regression | PASS | PDF uploads never enter a browser parser; a placeholder remains until the backend-safe PNG is available |
| Retention/recovery hardening | PASS | Periodic terminal-job cleanup, filesystem-first deletion/retry, and lifespan-only interrupted-job recovery covered by backend tests |
| PowerShell syntax | PASS | All scripts parsed successfully under PowerShell 5.1 |

The Windows runner now creates a unique current-user-owned `%TEMP%\docuverify-tests\run-<process-id>-<random-guid>` root for every invocation, with separate pytest and application-runtime children. This prevents collisions across Windows execution identities, does not require the inaccessible legacy `backend/runtime/pytest-temp` directory, leaves production runtime behavior unchanged, and needs no administrator access.

## Isolated live smoke verification

The application started through `scripts/run-local.ps1`, and the final post-hardening smoke ran through the frontend proxy. Health returned `ok`; diagnostics returned backend ready with GPU detected, `pymupdf_embedded_text`, and CPU execution.

| Run | Status | Risk | Findings | Pipeline | Wall | Localization/events |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| Tampered demo 1 | PASS | 93.6 | 1 | 1,990 ms | 2.042 s | Expected-region IoU 0.2201; 10 real stages |
| Clean comparison | PASS | 0 | 0 | 358 ms | 0.402 s | No suspicious region |
| Tampered demo 2 | PASS | 93.6 | 1 | 438 ms | 0.540 s | Stable repeat; 10 real stages |

The tampered pipeline produced a registered browser-safe evidence PNG. Clean risk was substantially below tampered risk, and repeated tampered scoring/finding count were deterministic. Backend access-log inspection showed only expected 200/202 responses and no errors.

## UI verification boundary

Frontend tests passed for initial upload state, demo initiation, progress updates, completed results, marker-to-evidence interaction, visible error handling, and the PDF preview security regression. Raw PDF uploads never enter a browser parser or local object URL; the UI retains a placeholder until the backend-safe PNG is available.

An in-app browser smoke was attempted, but no browser backend was connected. Therefore:

- No screenshot exists.
- No manual browser-console inspection result is claimed.
- No manual click-through claim is substituted for automated test evidence.

This unavailable optional test tool does not invalidate the passing frontend tests and independently verified live API pipeline.

## Acceptance gate

| # | Mandatory condition | Status | Evidence |
| ---: | --- | --- | --- |
| 1 | Local repository exists | PASS | Git repository on `main` |
| 2 | Frontend and backend install successfully | PASS | Full test/build workflow executed |
| 3 | Application starts locally | PASS | Started through `run-local.ps1` |
| 4 | Health endpoint succeeds | PASS | Live health returned `ok` |
| 5 | Diagnostics endpoint succeeds | PASS | Backend ready; provider/device/GPU returned safely |
| 6 | Reference and candidate can be submitted | PASS | Backend upload tests and live demo/clean job submission |
| 7 | PDF or image is rendered | PASS | Live page/evidence PNG generation |
| 8 | Golden demo runs through the real pipeline | PASS | Two live runs, each with real stages |
| 9 | Backend emits real progress events | PASS | 10 stages observed on both demo runs |
| 10 | Frontend displays progress | PASS | Passing frontend progress test |
| 11 | At least one actual alteration is detected | PASS | One finding, risk 93.6 |
| 12 | Marker overlaps expected altered region | PASS | IoU 0.2201 |
| 13 | Clicking marker shows explanation | PASS | Passing marker/evidence drawer test |
| 14 | Overall tampering risk appears | PASS | Live result and completed-result frontend test |
| 15 | Clean candidate is substantially lower risk | PASS | Clean 0 versus tampered 93.6 |
| 16 | Golden demo succeeds twice | PASS | Stable 93.6 risk and one finding twice |
| 17 | Backend tests pass | PASS | 34 passed on each of two consecutive runs |
| 18 | Frontend tests pass | PASS | 8 passed in 2 files |
| 19 | TypeScript check passes | PASS | Successful typecheck |
| 20 | Production frontend build passes | PASS | Vite 7.3.6, 435 modules, passed twice |
| 21 | No secrets, user documents, or model caches tracked | PASS | Ignore policy and targeted hygiene review; only fictional fixtures are intentional |
| 22 | Pre-approval Git status contained only intentional Phase 1 files | PASS | Runtime/cache outputs were ignored before staging |
| 23 | Pre-approval no-commit gate was respected | PASS | The repository remained at zero commits until explicit release approval |

## Remaining non-blocking limitations

- Single-page exact comparison only; multi-page input is rejected.
- Raster OCR is unavailable; embedded PDF text and visual comparison remain active.
- Simultaneous backend instances cannot safely share one runtime directory.
- Python 3.14 produces non-failing deprecation warnings in the current FastAPI stack.
- Python 3.14 compatibility with future OCR libraries must be addressed before raster/GPU OCR integration in Phase 2.
- Current golden-fixture localization IoU is 0.2201 and should be improved in Phase 2.
- No screenshot or manual browser-console artifact was captured.
- GitHub remote creation was not attempted because `gh` is unavailable.
- RTX 5060 smoke testing remains required.
- Future-phase features remain intentionally absent.

## Repository disposition

Phase 1 passed architectural review and is approved for the first commit plus annotated tag `phase-1-4h-demo`. GitHub remote configuration and push remain pending; RTX 5060 validation, raster/GPU OCR, improved localization, and other Phase 2 work remain outside this release-control operation. Recommended next action after commit and tag: **READY FOR REMOTE SETUP**.
