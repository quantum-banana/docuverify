# DocuVerify

> Upload the document. See the evidence. Trust only what can be explained.

DocuVerify is a local-first document investigation prototype. It compares a
questioned PDF or image with an issued original, an official template, or the
closest validated local DocuVault profile. It keeps visual tampering risk,
digital authenticity, reference strength, OCR confidence, logical consistency,
handwriting similarity, signature similarity, and coverage as separate
dimensions. None of those scores is a legal authenticity or identity decision.

## Current candidate line

- Phase 2 checkpoint: `VARIABLE-SUCKS-RC2` at
  `051d44ce00592e59ad04c84f21b917ebbdfa943f`
- Core-expansion work branch: `phase-3-core-expansion`
- Intended immutable review branch: `candidate/phase-3-core-rc1`
- Released Phase 1 baseline and tag remain unchanged
- Primary supported runtime: Python 3.12; Python 3.12.10 is the tested
  cross-laptop baseline
- Development machine: Windows with an NVIDIA RTX 4060 Laptop GPU
- OCR execution: RapidOCR/ONNX Runtime on CPU, with CPU as the mandatory
  raster fallback

The candidate branch is for review only. It is not merged into `main` and no
release tag is created by the core-expansion workflow.

## Three verification paths

### Compare with issued original

Exact mode expects the page set, text, values, layout, dimensions and visual
content to agree with a supplied known-good file. Missing, added, reordered and
dimension-mismatched pages remain explicit evidence.

### Compare with official template

Template mode permits ordinary values to vary while retaining forensic checks
on their typography, stroke weight, baseline, spacing, geometry, background,
residual text, erasure halos, compression and compositing. Fixed labels remain
strict. Variable content is not automatically suspicious, but variable-region
appearance still is.

### Find closest trusted profile

DocuVault mode accepts only the questioned document. It extracts local text and
layout evidence, searches validated profiles, ranks the top three, and explains
the score components. Matching uses issuer text, headings, layout anchors, page
geometry, fixed visual evidence when available, security regions, script and
profile completeness. Filenames are not matching evidence.

The bundled catalog contains 20 validated profiles spanning 19 document
families: 19 conservative official-source metadata/generic profiles and one
explicitly fictional visual showcase profile. A strong match does not prove
issuance. A closest-profile fallback is clearly labelled and must not be
treated as a trusted original.

## Core evidence checks

- Strict, versioned local profile validation with duplicate rejection,
  enable/disable state, deterministic SQLite indexing and safe optional
  `DOCUVAULT_PATH` loading
- Local PDF signature inspection with pyHanko: unsigned, valid but unknown
  trust, locally trusted, invalid, changed-after-signing and unsupported states
- An explicit local certificate trust store; no network certificate fetching
  and no implicit operating-system trust roots
- Local OpenCV QR detection/decoding, redacted payload summaries and digests,
  profile structure checks, visible-field consistency, geometry/compositing
  indicators and an explicit cryptographic-verification boundary
- Conservative PDF/image metadata and revision inspection without inventing an
  editor, website or provenance source
- Versioned profile rules for fixed text, regular expressions, numeric ranges,
  sums, percentages, date/age ordering, cross-page equality, QR equality,
  grades and status consistency
- Low-confidence OCR causes logical rules to skip and lowers coverage; it does
  not create tampering risk
- Local handwriting comparison with HOG/gradient, contour, skeleton, keypoint,
  texture, projection/spacing, slant, baseline and component features
- Local signature comparison using 2-5 trusted samples, conservative
  translation/scale/rotation normalization, closest-exemplar evidence and
  independent placement, scale, boundary, background and compositing checks
- A deterministic unified assessment that preserves the strongest page and
  never presents itself as an authenticity probability

Handwriting and signature similarity describe appearance consistency only.
They are not legal identity, authorship or handwriting-expert conclusions. A
copied genuine signature can remain visually similar, which is why compositing
evidence is reported separately.

## Inputs and resource limits

- PDF, PNG and JPEG
- 1-10 physical pages per PDF; images are single-page
- Up to 20 review slots when unmatched pages from both inputs must be shown
- Sequential page processing with bounded local rendering/OCR work
- 1-5 optional handwriting exemplars
- 2-5 optional signature exemplars
- Optional exact local profile-ID override for DocuVault mode

The frontend never parses uploaded PDFs. It displays only backend-rendered,
registered browser-safe images and evidence assets.

## Python and Windows bootstrap

The current dependency set is supported and tested on Python 3.12.10. Python
3.11 is not claimed merely because it is installed; the pinned NumPy set
requires Python 3.12 or newer. Python 3.14 installations and recoverable virtual
environment backups are preserved but are not selected automatically for OCR.

Bootstrap selection priority is:

1. explicit user override;
2. Python 3.12;
3. another interpreter only when the complete dependency set is proven
   compatible.

No system CUDA toolkit, NVIDIA driver, global Python installation or file
association is installed or modified. GPU OCR may be attempted only through a
separately supported provider/runtime combination. CPU raster OCR remains
mandatory.

From the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\diagnose-windows.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap-windows.ps1 -PythonVersion 3.12 -OcrProvider auto -OcrDevice cpu
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-local.ps1
```

Default local endpoints:

- Frontend: <http://127.0.0.1:5173>
- Health: <http://127.0.0.1:8000/api/v1/health>
- Diagnostics: <http://127.0.0.1:8000/api/v1/diagnostics>
- API documentation: <http://127.0.0.1:8000/api/docs>

## API outline

- `POST /api/v1/analyses/reference`: Exact or Template comparison
- `POST /api/v1/analyses/automatic`: candidate-only DocuVault retrieval
- `GET /api/v1/profiles`: validated local profile catalog/search
- `PATCH /api/v1/profiles/{profile_id}/state`: local enable/disable state
- `GET /api/v1/analyses/{job_id}`: persisted job/result state
- `GET /api/v1/analyses/{job_id}/events`: replayable backend-driven SSE
- `GET /api/v1/analyses/{job_id}/assets/{asset_id}`: allowlisted evidence

New real progress stages cover document-family identification, profile search,
issuer/layout ranking, code decoding, PDF signatures, metadata, logical fields,
handwriting, signatures and evidence aggregation. There are no artificial
delays.

## Validation

The RTX 4060 candidate gate passed with 120 backend tests, 23 frontend tests,
TypeScript typecheck, a 435-module production build and the integrated smoke.
The exact evidence is recorded in `docs/verification-status.md`.

Run the complete automated gate only after an integrated change set is ready:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-tests.ps1
python -m backend.scripts.smoke_core_expansion
git diff --check
```

The integrated smoke uses only committed fictional fixtures and ephemeral
in-memory/generated keys. It covers existing Exact and Template behavior,
multi-page and raster OCR, DocuVault strong matching and fallback, QR mismatch,
PDF signature trust/modification states, metadata, logical rules, handwriting,
signature appearance and pasted-signature compositing.

## Privacy and storage

- Analysis stays local; there is no automatic cloud document upload.
- Runtime uploads, OCR output, decoded payloads, SQLite files, crops, overlays,
  screenshots, caches, logs, trust material and generated evidence are ignored.
- Filenames are sanitized and assets are addressed by job/asset IDs, never by
  caller-supplied filesystem paths.
- QR payloads and sensitive logical values are redacted in report/log surfaces.
- Private test signing keys are generated ephemerally and are never committed.
- Use only fictional documents or material you are authorized to process.

## Known limitations

- Profile coverage is intentionally conservative and not universal across
  issuer, regional, language, year or delivery-channel variants.
- Nineteen official-source profiles contain metadata/structural descriptors,
  not redistributed official document bytes.
- Profile matching cannot verify personal field values or issuance.
- OpenCV QR is the active decoder; additional barcode providers report
  unsupported unless configured.
- QR cryptographic verification is unavailable without a profile-specific,
  locally implemented issuer specification and keys.
- An unsigned PDF is not proof of forgery; an unknown signer is not the same as
  a broken signature.
- Metadata can support or contradict provenance but usually cannot identify a
  specific editing website or person.
- OCR, page correspondence, auto-selected biometric regions and classical
  biometric similarity can be uncertain on low-quality input.
- Multiple backend processes must not share one runtime directory.
- Accounts, cloud deployment, public verification APIs, custom large-model
  training, H100 processing and blockchain/DocuLedger are excluded.

See [HANDOFF.md](HANDOFF.md), [docs/architecture.md](docs/architecture.md),
[backend/docuvault/README.md](backend/docuvault/README.md) and
[docs/dependency-licenses.md](docs/dependency-licenses.md) for the operational,
trust and source-provenance boundaries.
