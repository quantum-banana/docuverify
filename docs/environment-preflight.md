# Windows environment preflight

Captured on 2026-08-28 in the Asia/Calcutta time zone. This report is sanitized: it omits usernames, personal install paths, machine identifiers, tokens, and environment secrets.

## Observed machine

| Check | Sanitized result | Interpretation |
| --- | --- | --- |
| Windows edition | Windows 11 Home Single Language, x64 | Observed through the Windows/Node OS APIs |
| Windows version/build | 10.0.26200 | Observed; the compatibility version remains in the 10.0 family |
| CPU | AMD Ryzen 9 8945HS w/ Radeon 780M Graphics | Registry-reported friendly name |
| CPU identifier | AMD64 Family 25 Model 117, 16 logical processors | Observed environment/registry values |
| System RAM | 16,356,962,304 bytes (15.23 GiB observed) | Consistent with the brief's nominal 16 GB; binary observed capacity and supplied marketing capacity are intentionally distinguished |
| Free RAM at capture | Approximately 4.3 GiB | Volatile measurement; not a capacity figure |
| Workspace volume | 100.00 GiB total, approximately 77.9 GiB free at final verification | Observed for drive E:; free space changes over time |
| GPU | NVIDIA GeForce RTX 4060 Laptop GPU | `nvidia-smi` |
| Dedicated GPU memory | 8188 MiB | Driver-reported total, commonly described as 8 GB VRAM |
| NVIDIA driver | 610.62 | `nvidia-smi` |
| Reported CUDA compatibility | 13.3 | NVIDIA driver's UMD compatibility report; this does not confirm a local CUDA toolkit or a GPU-enabled Python package |

## Toolchain

| Check | Result | Required for Phase 1? |
| --- | --- | --- |
| Git | 2.54.0.windows.1 | Required for repository workflows; local runtime can start without Git |
| GitHub CLI (`gh`) | Unavailable | No; private-remote creation was not attempted |
| `gh auth status` | Not run because `gh` is unavailable | No |
| Node.js | 24.15.0 | Yes |
| npm | 11.12.1 through `npm.cmd` | Yes; `npm.cmd` avoids the local PowerShell execution-policy problem affecting npm's `.ps1` shim |
| Python launcher (`py`) | Present but reports no registered Python | Launcher result is misleading in this environment |
| WindowsApps `python` / `python3` aliases | Detected by command lookup but inaccessible during the initial sandbox probe | Not used as evidence of a runnable interpreter |
| Usable Python | 3.14.6, 64-bit | Direct invocation and pip association confirmed; personal path omitted |
| Project virtual environment | Python 3.14.6 | Created locally; backend imports and test execution verified |
| pip | 26.1.2 associated with Python 3.14 | Observed; personal path omitted |

Python 3.11 remains the conservative recommendation for a fresh machine. The Windows scripts do not trust command presence alone: they execute each candidate, prefer an existing project virtual environment, probe launcher selectors, and understand the per-user Python install-manager layout without hard-coding a username.

## OCR and execution mode

- Tesseract executable: not detected.
- Optional modules `pytesseract`, `easyocr`, `onnxruntime`, `paddleocr`, and `rapidocr_onnxruntime`: not detected.
- Initial virtual-environment imports were absent before bootstrap; final diagnostics found all mandatory backend imports available.
- Active Phase 1 text provider: `pymupdf_embedded_text`.
- Raster OCR capability: `false`; raster/no-text inputs use visual comparison without claiming OCR.
- Visual comparison device: CPU through OpenCV/NumPy.
- OCR execution device: CPU.
- NVIDIA GPU: detected by backend diagnostics (`gpu_detected=true`), but GPU OCR was not configured or claimed.

The final `scripts/diagnose-windows.ps1` run exited 0. The live diagnostics endpoint reported backend ready, `pymupdf_embedded_text`, CPU execution, raster OCR false, and GPU detected.

## Repository and remote state at capture

- Working repository: `docuverify` beneath the supplied workspace.
- Local Git repository: initialized on branch `main`.
- Base commit: none; Phase 1 remains uncommitted pending review.
- GitHub remote: none created because GitHub CLI is unavailable.
- Credentials/tokens: not queried or recorded.

## Reproduce the safe checks

    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\diagnose-windows.ps1

The verified diagnostic exited 0. It exits non-zero only when a required host component is unusable, or when a completed bootstrap is internally inconsistent. Missing GitHub CLI, GPU OCR, CUDA toolkit, and optional OCR packages are reported without failing the host preflight.
