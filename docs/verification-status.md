# Phase 2 verification status

**Current status: `PASS_ON_4060_PENDING_RTX5060`**

Phase 2 is an unreleased working-tree candidate on local branch `phase-2-work`, based on Phase 1 commit `ee2ad3ca7defe1010ac1d3f6be39bd5eee205392`. No Phase 2 commit, tag, push, merge, or release has occurred.

The final candidate gate passed on the RTX 4060 laptop. A separate RTX 5060 run passed the released Phase 1 baseline on Python 3.12.10. **RTX 5060 Phase 2-specific OCR and multi-page validation remain pending.** This status is suitable for candidate review; it is not final release readiness and does not authorize a commit, tag, push, or merge.

## Host and environment

| Check | Status | Exact observation |
| --- | --- | --- |
| Branch/base | PASS | `phase-2-work` at `ee2ad3ca7defe1010ac1d3f6be39bd5eee205392`; Phase 2 remains uncommitted |
| Released baseline | PASS | `main`, `origin/main`, and `phase-1-4h-demo` point to the approved Phase 1 commit |
| Windows | PASS | Windows 11 Home Single Language 10.0.26200, x64 |
| GPU | PASS FOR DEVELOPMENT HOST | RTX 4060 Laptop GPU, 8188 MiB, driver 610.62 |
| Available Python | PASS | 3.14.6, 3.12.10, and 3.11.9 |
| Primary supported Python | PASS | Python 3.12.10 is the tested cross-laptop baseline |
| Python 3.11 | UNSUPPORTED FOR CURRENT REQUIREMENTS | Installed availability is not a support claim; current dependencies require Python 3.12+ |
| Environment preservation | PASS | `.venv-phase1-py314` remains on Python 3.14.6 and `.venv-phase2-py311` remains on Python 3.11.9 |
| Node/npm | PASS | Node 24.15.0 and npm 11.12.1 |
| Raster OCR provider | PASS | RapidOCR 3.9.2 with ONNX Runtime 1.29.0 |
| OCR device | PASS | CPU |
| GPU OCR / VRAM | NOT ATTEMPTED / N/A | No GPU OCR, GPU timing, or VRAM before/after claim |
| RTX 5060 | PHASE 1 PASS / PHASE 2 PENDING | 8 GB VRAM; driver-reported CUDA 13.3; no system CUDA toolkit |

## Confirmed RTX 5060 Phase 1 smoke

The independent RTX 5060 result is authoritative for the released Phase 1 baseline:

| Check | Status | Exact observation |
| --- | --- | --- |
| Bootstrap/runtime | PASS | Python 3.11.9 was tried first and rejected by the current dependency set; Python 3.12.10 installed and ran the complete project |
| Backend suite | PASS | 34 tests passed |
| Frontend suite | PASS | 8 tests passed |
| Production build | PASS | Build completed successfully |
| Clean/tampered risk | PASS | `0` / `93.6` |
| Golden localization | PASS | IoU `0.2201` |
| Performance | OBSERVED | Approximately `0.4-0.6 s` |
| Repository hygiene | PASS | Repository remained clean |
| Compute path | PASS | CPU/OpenCV/PyMuPDF; no GPU OCR and no CUDA toolkit |

This result proves Phase 1 portability and the Python 3.12 baseline. It does not prove Phase 2 raster OCR, multi-page analysis, template behavior, or GPU OCR on the RTX 5060. CPU raster OCR remains mandatory; GPU OCR can be attempted only through a supported provider/runtime combination, without installing or modifying the system CUDA toolkit.

## Input and result-count contract

Each reference and candidate PDF is independently limited to 10 physical input pages; PNG/JPEG inputs remain single-page. Correspondence may expose up to 20 ordered review slots when both missing reference pages and added candidate pages must be represented. The review-slot union does not raise either upload's physical-page limit. Physical processing progress/counts remain distinct from the ordered result-slot count.

## Consolidated automated gate

| Check | Status | Exact observation |
| --- | --- | --- |
| Windows bootstrap selection policy | PASS | With real Python 3.11.9 and 3.12.10 installations present, automatic resolution selected Python 3.12.10; Python 3.14 was not selected automatically |
| Complete backend pytest suite | PASS | `81 passed in 36.25 s` |
| Complete frontend test suite | PASS | `14 passed in 2.52 s` |
| TypeScript typecheck | PASS | Exit 0 |
| Vite production build | PASS | 435 modules transformed in `1.90 s` |
| Build output | OBSERVED | CSS `45.84 kB` (`10.07 kB` gzip); JavaScript `379.26 kB` (`118.96 kB` gzip) |
| Phase 1 fixture integrity | PASS | All six Phase 1 asset hashes unchanged |
| Phase 2 fixture determinism | PASS | Manifest and all 25 Phase 2 artifact hashes identical after rerun |
| Phase 2 manifest | PASS | SHA-256 `3bb69928ea82896e7cd751500c396576176c67a5cbc7d50e8db452f03b409a50` |
| Production expected-mask isolation | PASS | Production runtime does not read test manifest/mask data |
| Repository hygiene | PASS | Diff/secret/runtime/cache/personal-path/large-file checks passed; no Phase 2 commit/tag/push/merge |

## Final application smokes

### Phase 1 regression and API

Health and diagnostics passed. The final Phase 2 tree preserved the Phase 1 behavior with improved localization.

| Candidate/run | Risk | Analysis duration | Wall time | Other evidence |
| --- | ---: | ---: | ---: | --- |
| First tampered | `86.3` | `1251 ms` | `1.275 s` | Finding localized; IoU `0.3745` |
| Clean | `0` | `459 ms` | `0.498 s` | No suspicious finding |
| Warm tampered | `86.3` | `565 ms` | `0.633 s` | 10 stages; evidence PNG returned |

The precise owned localization regression is IoU `0.3745511831`, improved from Phase 1's earlier `0.2201` and above the `0.30` target.

### Multi-page and correspondence

| Candidate | Risk | Analysis duration | Wall time | Other evidence |
| --- | ---: | ---: | ---: | --- |
| Three-page clean | `0` | `1158 ms` | `1.233 s` | All pages clean |
| Page-2 tampered | `85.3` | `1218 ms` | `1.288 s` | Page 2 carries the finding; IoU `0.5585` |
| Missing page | `82` | `1005 ms` | `1.057 s` | Explicit missing-page result |
| Added page | `78` | `1344 ms` | `1.395 s` | Explicit added-page result |
| Reordered pages | `70.4` | `1253 ms` | `1.306 s` | Explicit reordered result |

The precise owned page-2 localization regression is IoU `0.5584561077`.

### Exact and template comparison

| Mode/candidate | Risk | Result |
| --- | ---: | --- |
| Exact / legitimate variable changes | `92.9` | Exact mode appropriately treats changed values as mismatches |
| Template / legitimate variable changes | `15.0` | Four variable-region suggestions; legitimate changes remain low risk |
| Template / manipulated variable | `81` | Background-compositing evidence detected |

The exact/template risk gap for the legitimate candidate is `77.9`, and the manipulated candidate remains `66` points above the legitimate template result.

### Raster OCR

The deterministic raster-only PDF contains one page image and zero embedded characters. The standalone final smoke used `rapidocr_onnxruntime` on CPU and returned:

- mean confidence `0.997524705882353`;
- 17 recognized words;
- all 9 expected token/box matches;
- normalized boxes;
- cold call `2.210 s` and warm call `0.771 s`;
- provider initialization count `1`;
- process working set `81.64 MiB` before OCR, `209.66 MiB` after cold OCR, and `172.66 MiB` after warm OCR.

These are deterministic-fixture observations, not general OCR performance guarantees.

## Fresh-server performance and memory

A separate fresh-server workload produced the following observed values:

| Step | Analysis duration | Wall time | Working set | Private memory |
| --- | ---: | ---: | ---: | ---: |
| Baseline | — | — | `86.08 MiB` | `558.25 MiB` |
| Single-page tampered | `2488 ms` | `2.573 s` | `217.64 MiB` | `769.80 MiB` |
| Three-page clean | `1382 ms` | `1.455 s` | `219.59 MiB` | `773.44 MiB` |
| Three-page tampered | `1453 ms` | `1.502 s` | `217.95 MiB` | `772.02 MiB` |
| Cold raster job | `2887 ms` | `2.879 s` | — | — |
| Warm raster job 1 | `1787 ms` | `1.788 s` | `307.55 MiB` | `851.39 MiB` |
| Warm raster job 2 | `1742 ms` | `1.776 s` | `308.20 MiB` | `851.40 MiB` |
| Warm raster job 3 | `1803 ms` | `1.803 s` | `308.46 MiB` | `853.18 MiB` |
| Warm raster job 4 | `1786 ms` | `1.790 s` | `308.06 MiB` | `851.96 MiB` |
| Warm raster job 5 | `1781 ms` | `1.779 s` | `308.23 MiB` | `852.58 MiB` |

The warm raster working-set tail stayed within a `0.91 MiB` range. The bounded repeated workload showed no uncontrolled growth. GPU OCR was not attempted because the installed ONNX Runtime exposes no supported CUDA execution provider, so VRAM before/after is `N/A`.

## OCR semantics reviewed

The provider path is intentionally truthful:

- reliable embedded text is preferred;
- raster OCR returns text, normalized boxes, confidence, provider, and CPU device;
- the provider is cached rather than loaded per page;
- OCR failure remains a reported failure;
- visual analysis continues;
- coverage decreases;
- risk does not rise solely because OCR is missing.

Neither the detected RTX 4060 nor the RTX 5060 driver's CUDA 13.3 compatibility report constitutes GPU OCR or installed CUDA-toolkit evidence.

## UI/browser verification boundary

The final frontend automated gate covers the exact/template selector, multi-page progress, filmstrip, repeated page switching, page-specific markers, finding navigation, evidence details, variable-region suggestions, aggregate presentation, and page anomaly indicators.

The browser skill reported zero browser instances. Browser validation was therefore unavailable, screenshot paths are `NONE`, and no manual click-through or browser-console result is claimed. This explicit tooling boundary does not negate the automated frontend pass, but a reviewer requiring manual browser evidence must perform it in an environment with an available browser.

## Acceptance checklist

| # | Condition | Final candidate status |
| ---: | --- | --- |
| 1 | Phase 1 regression | PASS |
| 2 | Raster OCR | PASS ON CPU |
| 3 | Three-page processing | PASS |
| 4 | Page-2 localization | PASS — IoU `0.5584561077` |
| 5 | Filmstrip/navigation | PASS — automated frontend coverage |
| 6 | Marker alignment | PASS — owned localization and frontend coverage |
| 7 | Exact/template distinction | PASS |
| 8 | Legitimate variable handling | PASS |
| 9 | Manipulated variable detection | PASS |
| 10 | Missing/added/reordered pages | PASS |
| 11 | Golden IoU `>= 0.30` | PASS — `0.3745511831` |
| 12 | Clean/tampered risk separation | PASS |
| 13 | Backend tests | PASS — 81 |
| 14 | Frontend tests | PASS — 14 |
| 15 | Typecheck | PASS |
| 16 | Production build | PASS |
| 17 | API smoke | PASS |
| 18 | Multi-page smoke | PASS |
| 19 | OCR smoke | PASS ON CPU |
| 20 | Repository hygiene | PASS |
| 21 | No commit/push | PASS |
| 22 | RTX 5060 Phase 1 portability | PASS — 34 backend, 8 frontend, production build, risks `0/93.6`, IoU `0.2201` |
| 23 | RTX 5060 Phase 2 validation | PENDING — not substituted by the Phase 1 smoke |

## Repository disposition

- Phase 1 remains recoverable on local/remote `main` and tag `phase-1-4h-demo`.
- Phase 2 remains uncommitted, untagged, unpushed, and unmerged on `phase-2-work`.
- Candidate status is `PASS_ON_4060_PENDING_RTX5060`.
- The correct next step is review of the uncommitted Phase 2 candidate, not release.
- RTX 5060 Phase 1 portability passed; Phase 2-specific validation remains pending, so final release readiness is not claimed.

DocuVault remains Phase 3, signature/handwriting Phase 4, and blockchain Phase 6.
