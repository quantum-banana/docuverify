# DocuVerify handoff

## Current phase and release state

**Phase:** Phase 2 - eight-hour credible multi-page analyser<br>
**Candidate status:** **`PASS_ON_4060_PENDING_RTX5060` - UNRELEASED**<br>
**Local branch:** `phase-2-work`<br>
**Base commit:** `ee2ad3ca7defe1010ac1d3f6be39bd5eee205392`<br>
**Released baseline:** Phase 1 tag `phase-1-4h-demo` on the same commit<br>
**Remote:** private `origin` at `https://github.com/quantum-banana/docuverify.git`; `origin/main` points to the approved Phase 1 commit<br>
**Phase 2 commit/tag/push:** none

Phase 1 is already committed, tagged, and synchronized to the private remote. Phase 2 exists only as intentional working-tree changes on the local branch. The final RTX 4060 gate passed, but that result does not authorize merging to `main`, moving the Phase 1 tag, creating a Phase 2 tag, or pushing.

Development and the complete Phase 2 candidate gate occurred on the RTX 4060 laptop. A separate RTX 5060 smoke passed the released Phase 1 baseline and established Python 3.12 portability. **RTX 5060 Phase 2-specific OCR and multi-page validation remain pending.** Phase 2 is not final release-ready.

## Environment

- Windows 11 Home Single Language, x64, build 10.0.26200
- AMD Ryzen 9 8945HS, 16 logical processors, nominal 16 GB RAM
- NVIDIA GeForce RTX 4060 Laptop GPU, 8188 MiB
- NVIDIA driver 610.62; driver-reported CUDA compatibility 13.3
- Available Python lines observed: 3.11, 3.12, and 3.14
- Primary supported project runtime: Python 3.12.10
- Preserved Phase 1 environment: `.venv-phase1-py314`, Python 3.14.6
- Preserved pre-correction environment: `.venv-phase2-py311`, Python 3.11.9
- Node.js 24.15.0 and npm 11.12.1 through `npm.cmd`
- Raster OCR: RapidOCR 3.9.2 with ONNX Runtime 1.29.0, CPU
- GPU OCR: not attempted and not claimed

Python 3.12 is the tested cross-laptop baseline. The current dependency set is not compatible with Python 3.11, so merely having Python 3.11 installed is not a support claim. Bootstrap selection priority is an explicit user override, then Python 3.12; another version may be used only after the complete dependency set is proven compatible. Python 3.14 remains installed and its environment was moved aside, not deleted, but it is not selected automatically for Phase 2 OCR. No recoverable virtual-environment backup, NVIDIA driver, system CUDA toolkit, global file association, or global Python installation was removed.

## Phase 2 implementation candidate

- Accepts each PDF upload containing 1 through 10 physical pages and single-page PNG/JPEG images.
- Rejects PDFs above 10 physical pages, corrupt/encrypted PDFs, unsupported input, and unusable empty pages.
- Produces as many as 20 ordered review slots when correspondence must represent both missing reference pages and added candidate pages; this does not raise either upload's 10-page limit.
- Processes document pages sequentially by default with page-aware stage/progress events.
- Preserves Phase 1 exact comparison while adding template comparison.
- Represents fixed, variable, and unknown region roles.
- Suggests variable fields from stable labels and normalized OCR/layout geometry.
- Treats a consistent template value change as informational/allowed instead of automatically high risk.
- Retains typography, baseline, spacing, alignment, background, color, texture, and compositing checks for variable fields.
- Uses embedded PDF text when reliable and cached raster OCR for image-only pages.
- Continues visual analysis after OCR failure and lowers coverage instead of inventing OCR evidence or increasing risk solely because OCR is missing.
- Estimates page correspondence and exposes matched, missing, added, reordered, and dimension-mismatch states.
- Produces per-page risk, confidence, coverage, findings, OCR status, images, crops, and overlays plus a deterministic document aggregate.
- Adds page filmstrip/navigation, page risk badges, page-specific markers, finding-to-page navigation, anomaly indicators, and variable-region visualization.

## Exact and template behavior

In **Exact** mode, the candidate is expected to closely match the trusted reference. Page count/order, dimensions, text, values, layout, logos/seals, visual structure, metadata where available, OCR, and inserted/removed content can contribute evidence.

In **Template** mode, stable labels and layout are treated as fixed while suggested value regions may vary. A name, identifier, date, result, grade, mark, or score can change without dominating tampering risk when its appearance remains consistent. A pasted background or conspicuous type/baseline/spacing change remains suspicious even inside a variable field.

Automatic suggestions are heuristics, not general document understanding. Raster evidence does not claim exact font-family identification.

## OCR capability and failure semantics

- Born-digital PDF text provider: PyMuPDF embedded extraction when reliable
- Raster provider: cached RapidOCR/ONNX Runtime
- Device: CPU
- Provider initialization: reused across pages and analyses within the process
- Model files: package/cache data, not repository fixtures
- Failure behavior: visual pipeline continues, provider reports failure, page/document coverage falls, risk is not raised merely because OCR is unavailable

The deterministic raster-only PDF contains one page image and zero embedded text. Tested first on Python 3.12.10, the final direct OCR run returned 17 words and all 9 expected token/box matches with normalized boxes, mean confidence `0.997524705882353`, cold call `2.210 s`, warm call `0.771 s`, and initialization count `1`. Working set changed from `81.64 MiB` before OCR to `209.66 MiB` after the cold call and `172.66 MiB` after the warm call. These are local fixture observations, not general benchmarks.

## Deterministic fictional fixtures

Phase 1 files and their recorded hashes remain unchanged. Phase 2 adds:

- three-page reference and byte-identical clean candidate;
- three-page candidate changed only on page 2, with expected region and mask;
- missing-page, added-page, and reordered-page candidates;
- template reference, legitimate-variable candidate, and manipulated-variable candidate with expected region/mask;
- raster-only PNG/PDF with known OCR tokens and approximate normalized boxes.

All people, institutions, identifiers, results, logos, and seals are fictional. No internet document was collected.

The Phase 2 manifest SHA-256 is `3bb69928ea82896e7cd751500c396576176c67a5cbc7d50e8db452f03b409a50`. A full generator rerun left the manifest and all 25 recorded Phase 2 artifact hashes identical.

## Final RTX 4060 verification evidence

| Check | Current evidence |
| --- | --- |
| Phase 1 released baseline | Commit/tag/remote synchronization completed before Phase 2 |
| Python 3.12 runtime baseline | Tested across the RTX 4060 and RTX 5060 laptops; Python 3.11 is not claimed for the current dependencies |
| Bootstrap selection regression | PASS; real host with Python 3.11.9 and 3.12.10 selected Python 3.12.10 automatically |
| Consolidated backend suite | 81 passed in 36.25 seconds |
| Frontend suite | 14 passed in 2.52 seconds |
| TypeScript typecheck | PASS |
| Vite production build | PASS; 435 modules in 1.90 seconds; CSS 45.84 kB (10.07 kB gzip), JavaScript 379.26 kB (118.96 kB gzip) |
| Phase 1 golden localization | IoU `0.3745511831`, improved from `0.2201` and above the `0.30` target |
| Three-page page-2 localization | IoU `0.5584561077`; evidence remains on page 2 |
| Fixture determinism | Phase 2 manifest identical and all 25 file hashes identical after rerun |
| Phase 1 fixture integrity | All six Phase 1 hashes unchanged |
| Raster fixture structure | One image, zero embedded PDF text |
| API/Phase 1 smoke | Health/diagnostics passed; clean risk `0`; tampered risk `86.3`; IoU `0.3745`; first/warm tampered analysis `1251/565 ms` |
| Multi-page smoke | Clean risk `0`; page-2 tampered risk `85.3`, IoU `0.5585`; missing `82`; added `78`; reordered `70.4` |
| Template smoke | Exact legitimate `92.9`; template legitimate `15.0` with four variable suggestions; manipulated `81` with background-compositing evidence |
| Raster OCR invariant | Zero embedded characters; 17 words; 9 expected token/box matches; normalized boxes; confidence `0.997524705882353`; RapidOCR CPU initialization count `1` |
| Raster OCR timing | Cold `2.210 s`; warm `0.771 s` |
| Repository checks | PASS; no Phase 2 commit, tag, push, or merge |

The final RTX 4060 Phase 1 smoke took `1.275 s` wall for the first tampered run, `0.498 s` for clean, and `0.633 s` for the warm tampered run. The three-page clean and tampered smokes took `1.233 s` and `1.288 s` wall respectively. Analysis-reported durations were `1251`, `459`, `565`, `1158`, and `1218 ms` in the same order.

A fresh Python 3.12 server workload on the RTX 4060 host began at working set/private memory `86.08/558.25 MiB`. After the single-page tampered job it was `217.64/769.80 MiB`; after three-page clean and tampered jobs it was `219.59/773.44 MiB` and `217.95/772.02 MiB`. The cold full raster job took `2887 ms`; five subsequent warm jobs took `1787`, `1742`, `1803`, `1786`, and `1781 ms`. Their working-set tail stayed between `307.55` and `308.46 MiB`, a `0.91 MiB` range, with no uncontrolled growth. GPU OCR was not attempted, so VRAM before/after is `N/A`.

## Confirmed RTX 5060 Phase 1 portability evidence

The separate RTX 5060 smoke used Python 3.12.10 and completed the released Phase 1 project successfully:

| Check | Confirmed evidence |
| --- | --- |
| Backend tests | 34 passed |
| Frontend tests | 8 passed |
| Production build | PASS |
| Clean / tampered risk | `0` / `93.6` |
| Golden localization | IoU `0.2201` |
| Observed processing time | Approximately `0.4-0.6 s` |
| Repository state after smoke | Clean |
| Hardware | NVIDIA RTX 5060, 8 GB VRAM |
| CUDA observation | Driver-reported compatibility 13.3; no system CUDA toolkit installed |
| Phase 1 execution path | CPU/OpenCV/PyMuPDF |

This evidence closes the RTX 5060 Phase 1 portability check only. It does not claim RapidOCR, multi-page, template-mode, or other Phase 2 acceptance on that laptop. Raster OCR must be tested first on Python 3.12 with CPU OCR available as the mandatory fallback. GPU OCR may be attempted only through a supported provider/runtime combination; installing or modifying the system CUDA toolkit is out of scope.

## Browser verification boundary

The Phase 2 frontend includes automated coverage for mode selection, multi-page progress, filmstrip rendering, page switching, page-specific markers, finding navigation, evidence details, and anomaly presentation. The in-app browser backend was unavailable in this environment.

The browser skill found zero browser instances. Therefore browser verification was unavailable, screenshot paths are `NONE`, and no manual click-through or browser-console result is claimed. This remains an explicit tooling boundary if a reviewer requires manual browser evidence.

## Local commands

    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\diagnose-windows.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap-windows.ps1 -PythonVersion 3.12 -OcrProvider auto -OcrDevice cpu
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-local.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-tests.ps1

Local URLs default to frontend `http://127.0.0.1:5173`, backend `http://127.0.0.1:8000`, health `/api/v1/health`, diagnostics `/api/v1/diagnostics`, and API docs `/api/docs`.

## Known limitations

- Phase 2 passed the RTX 4060 acceptance gate but remains an unreleased local candidate.
- RTX 5060 Phase 1 portability passed; Phase 2-specific OCR and multi-page validation remain pending.
- OCR is verified on CPU only; CPU raster OCR is mandatory, and GPU OCR was not attempted.
- OCR quality depends on language, resolution, orientation, and scan quality.
- Page correspondence can be ambiguous when pages are visually/textually near-identical.
- Variable-region suggestions are heuristic.
- Maximum input is 10 PDF pages; images are single-page.
- Processing is sequential and local, not a distributed workload system.
- Multiple backend processes must not share one runtime directory.
- No manual browser screenshots or console inspection are available.
- Scores are evidence-guided investigative indicators, not legal authenticity probabilities.

## Deferred roadmap

- DocuVault remains Phase 3.
- Signature and handwriting enrollment/analysis remain Phase 4.
- Blockchain remains Phase 6.
- Accounts, public verification APIs, cloud deployment, custom model training, H100 processing, and unlimited OCR remain out of scope.

## Recommended next action

Review the uncommitted Phase 2 diff as a candidate while keeping the RTX 5060 boundary explicit: Phase 1 passed there, while Phase 2-specific validation remains pending. Do not commit, tag, push, merge, or start Phase 3 as part of this handoff. The candidate is ready for Phase 2 review, not final release.
