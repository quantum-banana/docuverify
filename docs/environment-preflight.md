# Windows environment preflight

Captured on 2026-08-28 in the Asia/Calcutta time zone for the Phase 2 working branch, with the later confirmed RTX 5060 Phase 1 smoke incorporated. This report is sanitized: personal installation paths, usernames, machine identifiers, tokens, and secrets are omitted.

## Observed machine

| Check | Sanitized result | Interpretation |
| --- | --- | --- |
| Windows | Windows 11 Home Single Language, x64, build 10.0.26200 | Phase 2 development host |
| CPU | AMD Ryzen 9 8945HS, 16 logical processors | Local sequential analysis host |
| RAM | 15.23 GiB observed; nominal 16 GB | Observed binary capacity and supplied nominal capacity are distinct |
| Workspace volume | 100 GiB total; free space is volatile | Local dependencies, OCR models, and evidence require space |
| GPU | NVIDIA GeForce RTX 4060 Laptop GPU | Only GPU present for Phase 2 development/verification |
| Dedicated GPU memory | 8188 MiB | Driver-reported total |
| NVIDIA driver | 610.62 | No driver change was made for Phase 2 |
| Driver CUDA compatibility | 13.3 | Does not prove a system CUDA toolkit or GPU-enabled OCR runtime |
| RTX 5060 Phase 1 host | NVIDIA GeForce RTX 5060, 8 GB VRAM | Phase 1 smoke passed; Phase 2-specific validation remains pending |
| RTX 5060 CUDA observation | Driver-reported compatibility 13.3; no system toolkit installed | Phase 1 remained CPU/OpenCV/PyMuPDF; this is not GPU OCR evidence |

## Repository state

- Current local branch: `phase-2-work`
- Branch base/HEAD commit: `ee2ad3ca7defe1010ac1d3f6be39bd5eee205392`
- Phase 1 tag: `phase-1-4h-demo`
- `main` and `origin/main`: approved Phase 1 commit
- Remote: private `https://github.com/quantum-banana/docuverify.git`
- Phase 2 commit, tag, merge, and push: none

GitHub CLI 2.98.0 was installed for the successful private-remote setup. A terminal opened before installation may not see `gh` until its `PATH` is refreshed; that does not mean the remote is absent. Tokens are not printed or stored in this report.

## Toolchain

| Check | Result |
| --- | --- |
| Git | 2.54.0.windows.1 |
| Node.js | 24.15.0 |
| npm | 11.12.1 through `npm.cmd` |
| Available Python versions | 3.11.9, 3.12.10, and 3.14.6 |
| Primary supported `.venv` runtime | Python 3.12.10 |
| Python 3.11 status | Installed, but unsupported by the current dependency set |
| Preserved `.venv-phase1-py314` | Python 3.14.6 |
| Preserved `.venv-phase2-py311` | Python 3.11.9; retained as a recoverable pre-correction environment, not a support claim |
| RapidOCR | 3.9.2 |
| ONNX Runtime | 1.29.0 |
| OCR device | CPU |

## Python environment strategy

Phase 1 originally ran on Python 3.14.6. That environment remains preserved as `.venv-phase1-py314`; it was renamed only after services were stopped and the replacement strategy was gated. The pre-correction Python 3.11 environment is also preserved as `.venv-phase2-py311`. Neither environment was deleted, and other recoverable virtual-environment backups must also be preserved.

The earlier bootstrap selected Python 3.11.9 first, but dependency installation failed because the committed NumPy requirement needs Python 3.12 or newer. Python 3.12.10 then installed and ran the complete Phase 1 project on the RTX 5060. Python 3.12 is therefore the tested cross-laptop baseline and the primary Phase 2 runtime. Python 3.11 availability is not a support claim; retaining 3.11 support would require a separately compatible constraint and its own complete test gate. Python 3.14 remains installed but is not selected automatically for Phase 2 OCR. No global Python installation or file association was removed.

The Windows bootstrap:

- gives an explicit `-PythonExecutable`/`-PythonVersion` user override first priority;
- otherwise selects Python 3.12;
- considers another version only when the complete dependency set is proven compatible;
- does not select Python 3.11 for the current requirements or automatically select Python 3.14 for Phase 2 OCR;
- reports the selected interpreter/version with personal paths suppressed;
- reuses an existing `.venv` only when it is complete and matches the requested version;
- refuses to overwrite an incomplete or version-mismatched environment;
- accepts OCR provider `auto`, `rapidocr`, or `none`;
- keeps CPU raster OCR as the mandatory fallback and fails clearly for an unsupported `-OcrDevice gpu` request;
- is safe to rerun and preserves existing recoverable virtual-environment backups.

Recommended command:

    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap-windows.ps1 -PythonVersion 3.12 -OcrProvider auto -OcrDevice cpu

An explicit interpreter can be selected without changing global associations:

    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap-windows.ps1 -PythonExecutable <absolute-python.exe> -PythonVersion 3.12 -OcrProvider rapidocr -OcrDevice cpu

## OCR compatibility and device

Phase 2 uses RapidOCR 3.9.2 with ONNX Runtime 1.29.0 on CPU.

- Born-digital PDFs use PyMuPDF embedded text when reliable.
- Raster/image-only pages use the cached RapidOCR provider.
- Raster OCR is tested first on Python 3.12, and CPU execution remains the mandatory fallback.
- Provider initialization is reused; it is not repeated per page.
- GPU OCR was not attempted.
- NVIDIA GPU detection and driver CUDA compatibility do not imply OCR acceleration.
- GPU OCR may be attempted only through a supported provider/runtime combination; the current bootstrap and application reject an unsupported GPU configuration.
- No system CUDA toolkit was installed or modified.
- Model/cache/runtime outputs are ignored and must not be committed.

The raster-only fixture has zero embedded PDF text. Tested first on Python 3.12.10, the final direct run recognized 17 words and all 9 expected token/box matches with normalized boxes. Mean confidence was `0.997524705882353`; the cold call took `2.210 s`, the warm call `0.771 s`, and the provider initialization count remained `1`. Working set was `81.64 MiB` before OCR, `209.66 MiB` after the cold call, and `172.66 MiB` after the warm call.

These are local fixture observations, not general OCR benchmarks. GPU OCR was not attempted, so GPU timing and VRAM before/after are `N/A`.

## Truthful OCR failure behavior

If raster OCR is unavailable or fails on one page:

1. the OCR result remains unsuccessful and identifies the provider/error boundary;
2. the visual comparison continues;
3. page and document analysis coverage decrease;
4. OCR absence alone does not raise tampering risk;
5. the UI must not describe that page as successfully OCR-processed.

Use `-OcrProvider none` only when an intentional visual-only installation is acceptable.

## Page and memory boundaries

- PDF input limit: 10 physical pages maximum per upload
- Image input: one PNG/JPEG page
- Ordered result limit: up to 20 review slots when missing reference and added candidate pages both need representation
- Processing strategy: sequential by default
- OCR engine: cached and reused
- Runtime: local in-process executor and private SQLite/job directories
- Cross-process sharing: unsupported; do not point multiple backend instances at one runtime directory

The 10-physical-page cap applies independently to the reference and candidate. It is not contradicted by a result containing up to 20 ordered review slots: those slots are the correspondence union of unmatched pages from both bounded inputs. The cap and sequential strategy are safeguards for the supplied 16 GB RAM environment; they do not represent unlimited or distributed processing.

## Diagnostics and verification boundary

Run the sanitized diagnostic with:

    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\diagnose-windows.ps1

Run the consolidated automated gate with:

    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-tests.ps1

The final RTX 4060 candidate gate has status `PASS_ON_4060_PENDING_RTX5060`. The bootstrap-selection regression passed on the real host with Python 3.11.9 and 3.12.10 both installed, resolving Python 3.12.10 automatically. The consolidated backend suite reported `81 passed in 36.25 s`; the frontend suite reported `14 passed in 2.52 s`; TypeScript typecheck passed; and the Vite build processed 435 modules in `1.90 s`, producing CSS `45.84 kB` (`10.07 kB` gzip) and JavaScript `379.26 kB` (`118.96 kB` gzip).

API health and diagnostics passed. Final-tree smokes recorded Phase 1 clean/tampered risk `0/86.3`, three-page clean/page-2-tampered risk `0/85.3`, missing/added/reordered risk `82/78/70.4`, and template exact-legitimate/template-legitimate/manipulated risk `92.9/15.0/81`. Localization IoU was `0.3745` for the Phase 1 tamper and `0.5585` for the page-2 tamper.

A fresh Python 3.12 server workload began at working set/private memory `86.08/558.25 MiB`. Working/private memory was `217.64/769.80 MiB` after a single-page tampered job, `219.59/773.44 MiB` after three-page clean, and `217.95/772.02 MiB` after three-page tampered. The cold raster job took `2887 ms`; five warm raster jobs took `1787`, `1742`, `1803`, `1786`, and `1781 ms`. Their working-set tail remained between `307.55` and `308.46 MiB`, a `0.91 MiB` range, showing no uncontrolled growth in this bounded run.

The browser skill found zero browser instances. Browser validation was unavailable, screenshot paths are `NONE`, and no manual click-through or browser-console result is claimed.

## Cross-laptop boundary and roadmap

The RTX 5060 Phase 1 smoke passed 34 backend tests, 8 frontend tests, and the production build. Clean/tampered risk remained `0/93.6`, IoU remained `0.2201`, observed processing was approximately `0.4-0.6 s`, and the repository remained clean. This establishes the Python 3.12 Phase 1 portability baseline; it does not replace an RTX 5060 Phase 2 OCR/multi-page acceptance run. Do not release Phase 2 based on the Phase 1 RTX 5060 evidence if the release process requires Phase 2 validation there.

DocuVault remains Phase 3, signature/handwriting work Phase 4, and blockchain Phase 6. No driver/CUDA work should be started to pull those phases forward.
