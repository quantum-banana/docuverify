# Core-expansion candidate verification status

**Current status: `READY_FOR_FINAL_MANUAL_REVIEW`**

The complete local core expansion was validated on the RTX 4060 development
machine on 2026-08-29. The work branch is `phase-3-core-expansion`; the intended
immutable review branch is `candidate/phase-3-core-rc1`. This candidate is not
merged into `main` and has no release tag.

## Release boundary

| Check | Status | Exact observation |
| --- | --- | --- |
| Approved Phase 2 checkpoint | PASS | `VARIABLE-SUCKS-RC2` at `051d44ce00592e59ad04c84f21b917ebbdfa943f`, tree `e0af6c8b4a78de8c9cdcdfee58e01ad00b785e75` |
| Phase 2 manual review | PASS | Legitimate cross-person values remained allowed; manipulated variable-field appearance remained localized |
| Core-expansion work line | PASS | `phase-3-core-expansion`, created directly from the approved checkpoint |
| Main/tags/existing candidates | PROTECTED | No merge, tag move, candidate rewrite or force-push is part of this workflow |
| Blockchain/DocuLedger | EXCLUDED | Reserved for a possible later phase |

## Host and runtime

| Check | Status | Exact observation |
| --- | --- | --- |
| Development host | PASS | Windows x64 laptop with NVIDIA GeForce RTX 4060 Laptop GPU, approximately 8 GB VRAM |
| Primary Python | PASS | Python 3.12.10 |
| Bootstrap selection | PASS | Explicit override first, then Python 3.12; 3.12 wins when 3.11 and 3.12 are both installed, and 3.14 is not selected automatically |
| Dependency consistency | PASS | `pip check` reported no broken requirements |
| Raster OCR | PASS | RapidOCR 3.9.2 and ONNX Runtime 1.29.0 on CPU |
| Added dependencies | PASS | `jsonschema==4.26.0`; `pyHanko==0.36.2` |
| System CUDA changes | NONE | No toolkit or driver installation/modification |
| GPU OCR | NOT CLAIMED | CPU raster OCR remains mandatory |

Python 3.11 is not claimed for the current pinned dependency set. Existing
Python 3.14 installations and recoverable virtual-environment backups remain
preserved but are not automatic Phase 2/core-expansion OCR selections.

## Implemented verification surface

- Existing Exact and Template paths remain available, including multi-page
  correspondence, page anomalies, variable-region appearance forensics and
  page-specific evidence.
- Candidate-only DocuVault retrieval validates and indexes 20 local profiles
  spanning 19 document families, returns an explained top three and always
  exposes a clearly labelled closest-profile fallback.
- Digital evidence separates PDF integrity from local signer trust and
  separates QR decoding from issuer cryptographic verification.
- Metadata and logical rules report conservative, explainable evidence; low OCR
  confidence skips affected rules instead of creating forgery risk.
- Handwriting accepts 1-5 exemplars. Signatures accept 2-5 exemplars and keep
  appearance consistency separate from placement/compositing evidence.
- The frontend exposes all three paths and uses progressive disclosure for the
  new evidence dimensions while preserving the approved white/red/limited-black
  design.

## Consolidated candidate gate

The initial gate exposed one stale SSE stage expectation. Its failed node was
updated for the real handwriting and signature stages and passed in isolation.
The integrated smoke then exposed a Windows SQLite-handle cleanup defect. The
job-store connection context now closes every handle deterministically; its new
focused regression and the smoke both passed. After those failed subsets were
green, one final complete regression was run.

| Check | Status | Exact observation |
| --- | --- | --- |
| Windows bootstrap selection policy | PASS | Python 3.12 selected over 3.11; Python 3.14 not selected automatically |
| Python runtime baseline | PASS | Python 3.12.10; dependency consistency passed |
| Complete backend suite | PASS | `120 passed in 50.23s` |
| Complete frontend suite | PASS | 2 files, `23 passed`; Vitest duration `4.29s` |
| TypeScript typecheck | PASS | Exit 0 |
| Production build | PASS | 435 modules transformed in `1.95s` |
| Build output | OBSERVED | HTML `0.60 kB` (`0.35 kB` gzip); CSS `42.80 kB` (`8.40 kB` gzip); JavaScript `393.33 kB` (`122.33 kB` gzip) |
| Test-runtime cleanup | PASS | Unique external pytest/application runtime removed after the gate |
| Integrated smoke | PASS | Exit 0; fictional fixtures and ephemeral signing material only |

## Integrated smoke evidence

| Area | Exact observation |
| --- | --- |
| Exact | Clean risk `0.0`; tampered risk `84.6` |
| Template | Legitimate variable changes risk `8.1`; manipulated variable appearance risk `72.0` |
| Multi-page | Page 2 retained one localized finding |
| Raster OCR | `rapidocr_onnxruntime`; 17 words |
| DocuVault strong match | `in.cbse.class10.generic.v1`, score `79.2`, three explained matches |
| Closest fallback | `synthetic.lumen-grove.achievement-record.v1`; fallback flag explicit |
| QR | Visible-field mismatch failed as expected; payload remained redacted |
| PDF signatures | Unknown trust, locally trusted and signed-content-modified states all distinguished |
| Metadata | Reversed creation/modification timeline failed as expected |
| Logical rules | 2 failed, 2 skipped |
| Handwriting | Consistent `79.9`; mismatch `53.6` and failed |
| Signature | Consistent `99.7`; mismatch `32.2` and failed; pasted-region compositing `66.4` |
| Progress | All ten new backend stages observed |

The self-signed unknown-trust validation and deliberately modified signed PDF
produce diagnostic messages from pyHanko during the smoke. Their asserted
outcomes are respectively valid-with-unknown-trust and signed-content-modified;
the smoke exits successfully.

## Confirmed RTX 5060 Phase 1 evidence

A separate RTX 5060 laptop previously passed the released Phase 1 baseline on
Python 3.12.10:

- 34 backend tests and 8 frontend tests passed;
- production build passed;
- clean/tampered risk remained `0` / `93.6`;
- localization IoU remained `0.2201`;
- observed processing was approximately `0.4-0.6 s`;
- the repository remained clean;
- the machine had 8 GB VRAM and driver-reported CUDA compatibility 13.3;
- no system CUDA toolkit was installed;
- processing remained CPU/OpenCV/PyMuPDF.

This is Phase 1 portability evidence only. No RTX 5060 core-expansion run or
performance benchmark is claimed.

## Privacy, provenance and limitations

- No real personal document, screenshot, OCR output, runtime database, cache,
  credential, private key or personal absolute path belongs in the candidate.
- The detached DocuVault transfer contained no `.git`; its 145-text-file source
  fingerprint is
  `15ece9ac94b5d40aaf721275c1c2ecca22b9aac599cc6675bfdc26b171e619d2`.
- No official document binary was imported. Official-source profiles contain
  conservative metadata/structural descriptors; the only bundled visual
  reference is explicitly fictional.
- Profile similarity cannot prove issuance. QR decoding is not cryptographic
  verification. Unknown signer trust is not invalid cryptography. Handwriting
  and signature similarity are not legal authorship or identity proof.
- Profile coverage, OCR, page correspondence and automatically selected
  biometric regions remain input-quality and variant dependent.
- Metadata normally cannot identify a particular editing website or person.

The next action is the final local manual showcase review of the immutable
candidate. Do not merge it into `main` or create a release tag during review.
