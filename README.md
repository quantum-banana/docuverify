# DocuVerify

> Upload the document. See the evidence. Trust what can be explained.

DocuVerify is a local-first document comparison prototype. It compares a trusted reference with a questioned PDF or image, reports bounded tampering risk separately from confidence and coverage, and keeps every finding tied to inspectable page evidence. It is an investigative aid, not a legal authenticity determination.

## Repository checkpoint

- Released baseline: Phase 1 commit `ee2ad3ca7defe1010ac1d3f6be39bd5eee205392`
- Protected tag: `phase-1-4h-demo`
- Private remote: `https://github.com/quantum-banana/docuverify.git`
- Current local branch: `phase-2-work`, created directly from the Phase 1 commit
- Phase 2 release state: **unreleased, uncommitted, untagged, and not pushed**
- Verified Phase 2 development machine: Windows with an NVIDIA RTX 4060 Laptop GPU
- RTX 5060 validation: **Phase 1 PASS; Phase 2-specific validation pending**

The Phase 2 candidate passed the complete RTX 4060 automated and smoke gate with status `PASS_ON_4060_PENDING_RTX5060`. A separate RTX 5060 smoke confirms the released Phase 1 baseline and Python 3.12 portability, but it is not a Phase 2 OCR/multi-page acceptance run. Phase 2 therefore remains unreleased and is not final release-ready: do not merge it to `main`, create a Phase 2 tag, or push it until review and the remaining RTX 5060 Phase 2 validation are handled.

## Phase 2 implementation

The working tree extends the Phase 1 vertical slice with:

- PDF analysis from 1 through 10 pages and single-page PNG/JPEG input
- Sequential, bounded page processing with page-aware backend progress
- Exact and template comparison modes
- Fixed, variable, and unknown region roles with automatic variable-region suggestions
- Embedded-text extraction for reliable born-digital PDF text
- Cached RapidOCR 3.9.2 with ONNX Runtime 1.29.0 for raster/image-only pages on CPU
- Per-page risk, confidence, coverage, status, OCR summary, findings, and evidence assets
- Page correspondence plus missing, added, reordered, and dimension-mismatch indicators
- Document aggregation that preserves strong evidence from any page
- Improved text-assisted localization mapped into normalized candidate-page coordinates
- A page filmstrip, page navigation, page-specific markers, finding-to-page navigation, and evidence details
- Deterministic fictional fixtures for clean/tampered three-page documents, page anomalies, template variables, and raster OCR

Each uploaded PDF is limited to 10 physical pages; corrupt or encrypted PDFs, unsupported formats, and unusable empty pages are rejected with structured errors. A comparison can expose up to 20 ordered review slots when missing reference pages and added candidate pages must both be represented. Processing remains sequential so full-resolution intermediates can be released page by page.

## Comparison modes

### Exact

Exact mode expects the candidate to closely match the trusted reference. Fixed text, values, layout, dimensions, logos, seals, visual structure, OCR output, page count, and page order may all contribute evidence.

### Template

Template mode distinguishes document structure from values that are expected to vary. Practical OCR/layout heuristics suggest regions after stable labels such as name, identifier, date, result, grade, mark, or score.

A visually consistent value change is treated as an allowed or informational variable-value difference rather than high-risk tampering. Typography, baseline, spacing, alignment, pasted-background boundaries, color, texture, or compositing inconsistencies can still make a variable field suspicious. Raster analysis does not claim exact font-family identification.

## OCR behavior

The default configuration is:

    DOCUVERIFY_OCR_PROVIDER=auto
    DOCUVERIFY_OCR_DEVICE=cpu

Reliable embedded PDF text is preferred. Image-only pages use one cached RapidOCR/ONNX Runtime provider instead of reloading models for every page. CPU raster OCR is the mandatory fallback. GPU OCR may be attempted only with a separately supported provider/runtime combination; it is not implied by an NVIDIA GPU or a driver-reported CUDA version. No system CUDA toolkit installation or modification is required or authorized for this phase.

OCR failure is truthful and non-fatal: the page continues through visual analysis, the provider reports failure, and analysis coverage is reduced. Missing OCR does not automatically raise tampering risk and is never reported as a successful OCR result.

The deterministic raster fixture has zero embedded PDF text. The Python 3.12 CPU smoke recognized its expected heading and field content with normalized boxes, mean confidence `0.997524705882353`, 17 words, and 9 expected token/box matches. The cold call took `2.210 s`, the warm call `0.771 s`, and the provider initialized once. These timings are fixture-specific observations, not performance guarantees.

## Windows prerequisites

- Windows 10 or Windows 11 x64
- Windows PowerShell 5.1 or PowerShell 7
- Python 3.12 x64; Python 3.12.10 is the tested cross-laptop baseline
- A maintained Node.js/npm installation compatible with the lockfile
- Git for repository workflows
- Enough local space for dependencies, OCR models, and rendered evidence

The current dependency set requires Python 3.12 or newer and is tested on Python 3.12.10. Python 3.11 is not a supported runtime for this dependency set. The former Python 3.14 Phase 1 environment is preserved locally as `.venv-phase1-py314`, and the pre-correction Python 3.11 environment is preserved as `.venv-phase2-py311`; neither was deleted. Python 3.14 remains installed but is not selected automatically for Phase 2 OCR. No recoverable virtual-environment backup, global file association, NVIDIA driver, or system CUDA toolkit should be removed or changed.

## Quick start

From the repository root:

    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\diagnose-windows.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap-windows.ps1 -PythonVersion 3.12 -OcrProvider auto -OcrDevice cpu
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-local.ps1

Bootstrap selection priority is an explicit user override, then Python 3.12. Another interpreter may be used only after the complete dependency set is proven compatible; Python 3.11 is not selected for the current requirements, and Python 3.14 is preserved but not selected automatically for Phase 2 OCR. Bootstrap reports the selected version, safely reuses a matching `.venv`, preserves recoverable environment backups, and refuses to overwrite an incomplete or version-mismatched environment. Use `-OcrProvider none` only when a visual-only installation is intentional.

Open:

- Frontend: <http://127.0.0.1:5173>
- Backend health: <http://127.0.0.1:8000/api/v1/health>
- Safe diagnostics: <http://127.0.0.1:8000/api/v1/diagnostics>
- Interactive API documentation: <http://127.0.0.1:8000/api/docs>

Press Ctrl+C in the run-script terminal to stop both services.

## Use the application

1. Choose Exact or Template comparison.
2. Select a trusted reference and questioned candidate.
3. Review the detected page counts and OCR capability indicator.
4. Start analysis and follow real page/stage progress.
5. Use the result filmstrip to select a page, inspect its risk badge and anomaly status, and open findings or suggested variable regions.

Markers are rendered only for the selected page. Finding selection navigates to the corresponding page before opening the evidence details. Missing candidate pages use an explicit placeholder rather than a fabricated preview.

Raw PDF uploads are never passed to a browser PDF parser or exposed as local object URLs. The frontend consumes only backend-rendered browser-safe image assets.

## Verification state

The Phase 1 commit remains the released, reproducible baseline. The unreleased Phase 2 candidate completed its RTX 4060 acceptance run with status **`PASS_ON_4060_PENDING_RTX5060`**:

- Bootstrap regression: pass; with Python 3.11.9 and 3.12.10 both installed, automatic selection resolved Python 3.12.10
- Backend: `81 passed in 36.25 s`; frontend: `14 passed in 2.52 s`
- TypeScript typecheck: pass; Vite production build: 435 modules in `1.90 s`
- Phase 1 final-tree smoke: clean risk `0`; tampered risk `86.3`; IoU `0.3745`
- Three-page smoke: clean risk `0`; page-2 tampered risk `85.3`; IoU `0.5585`; missing `82`; added `78`; reordered `70.4`
- Template smoke: exact legitimate `92.9`; template legitimate `15.0` with four variable suggestions; manipulated `81` with background-compositing evidence
- Raster smoke: RapidOCR/ONNX Runtime on CPU, zero embedded text, expected normalized content/boxes, one provider initialization
- Fixture manifest SHA-256: `3bb69928ea82896e7cd751500c396576176c67a5cbc7d50e8db452f03b409a50`; all 25 Phase 2 artifact hashes and all six Phase 1 fixture hashes remained unchanged
- Warm memory tail stabilized across repeated raster jobs; GPU OCR was not attempted, so VRAM evidence is not applicable

The independent RTX 5060 Phase 1 smoke passed all 34 backend tests, all 8 frontend tests, and the production build on Python 3.12.10. Clean risk remained `0`, tampered risk remained `93.6`, IoU remained `0.2201`, and observed processing was approximately `0.4-0.6 s`; the repository remained clean. That laptop has 8 GB VRAM and reports CUDA compatibility 13.3 through its driver, but no system CUDA toolkit is installed and Phase 1 remained CPU/OpenCV/PyMuPDF.

The in-app browser capability reported zero browser instances. Browser validation was therefore unavailable and screenshot paths are `NONE`; no manual click-through or browser-console result is claimed. RTX 5060 Phase 2-specific OCR and multi-page validation remain pending, so the Phase 1 portability evidence does not make Phase 2 released or final release-ready.

Run the consolidated automated checks with:

    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-tests.ps1

See [HANDOFF.md](HANDOFF.md) and [docs/verification-status.md](docs/verification-status.md) before treating any candidate result as an acceptance result.

## Privacy and storage

- Analysis stays local; there is no automatic cloud document upload.
- Uploads, SQLite state, page renders, crops, overlays, logs, OCR/model caches, and other runtime artifacts remain under ignored local paths.
- Filenames are sanitized and registered assets are addressed by job/asset identifiers, not arbitrary filesystem paths.
- Retention cleanup is configurable and limited to terminal jobs.
- Use only fictional documents or documents you are authorized to process.

## Known limitations

- Maximum 10 physical pages per PDF upload; images remain single-page inputs. A result can contain up to 20 ordered review slots when unmatched pages from both uploads must be shown.
- Page correspondence is heuristic and can be ambiguous for near-identical pages.
- Template variable-region suggestions are practical heuristics, not semantic document understanding.
- OCR quality depends on scan resolution, orientation, language, and image quality.
- OCR failure lowers coverage; it can leave text-specific checks unavailable.
- GPU OCR was not attempted; current OCR is RapidOCR/ONNX Runtime on CPU, which remains the mandatory raster fallback.
- Simultaneous backend processes must not share one runtime directory.
- Findings are investigative indicators, not authenticity probabilities, authorship claims, or legal conclusions.
- Manual in-app browser QA and screenshots are unavailable in this environment.
- RTX 5060 Phase 1 portability passed; Phase 2-specific OCR and multi-page validation remain pending.

## Roadmap boundary

- Phase 3: DocuVault and approved collection workflows
- Phase 4: signature and handwriting enrollment/analysis
- Phase 6: blockchain-related work

Accounts, cloud deployment, public verification APIs, custom model training, H100 processing, and unlimited OCR remain outside Phase 2.
