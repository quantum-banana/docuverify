# DocuVerify core-expansion handoff

## Release boundary

This work starts from the manually approved Phase 2 repair checkpoint on
`VARIABLE-SUCKS-RC2`:

- commit: `051d44ce00592e59ad04c84f21b917ebbdfa943f`
- tree: `e0af6c8b4a78de8c9cdcdfee58e01ad00b785e75`
- parent: `3c7b6f653efcdd0b82b0a6859cd356eea707be43`
- manual Template forensic review: PASS

The active development line is `phase-3-core-expansion`. The intended immutable
review line is `candidate/phase-3-core-rc1`. The workflow must not merge into
`main`, move an existing candidate, alter a tag or force-push.

## Environment

- Windows 11 x64
- NVIDIA GeForce RTX 4060 Laptop GPU, approximately 8 GB VRAM
- Python 3.12.10 project runtime
- RapidOCR 3.9.2 / ONNX Runtime 1.29.0 on CPU
- OpenCV and PyMuPDF for visual/PDF work
- Node.js/npm from the committed frontend lockfile
- Added Python dependencies: `jsonschema==4.26.0` and `pyHanko==0.36.2`
- No system CUDA toolkit installation or modification
- No GPU OCR claim; CPU OCR is mandatory

Python 3.12 is the tested cross-laptop baseline. The dependency set is not
claimed on Python 3.11. Bootstrap selects an explicit override first, then
Python 3.12, and another interpreter only after full compatibility is proven.
Python 3.14 and recoverable virtual-environment backups remain preserved but are
not automatic OCR selections.

## Completed core implementation

### Existing comparison behavior

- Exact and Template modes remain available.
- Template variable values may change while weight, baseline, spacing,
  geometry, background, residual text, erasure halo, compression and
  compositing remain testable.
- Multi-page correspondence and missing, added, reordered and dimension states
  remain explicit.
- Strong evidence on one page is not averaged away by clean pages.
- Every suspicious finding retains page, region, category, severity, risk,
  confidence, explanation, measurements, crops, overlay and source.

### DocuVault

- 20 strictly validated profiles covering 19 families
- 19 conservative official-source metadata/generic profiles
- one fictional hash-bound visual showcase profile
- strict Draft 2020-12 schema and semantic duplicate checks
- deterministic local SQLite index and enable/disable state
- safe optional `DOCUVAULT_PATH` loading without link/path escape
- top-three matching with component explanations
- issuer text, headings, anchors, geometry, fixed visual features, security
  regions, script and completeness as independent inputs
- no filename matching
- explicit strong, moderate and closest-available tiers
- optional exact profile override

### Digital, code, metadata and logical evidence

- pyHanko PDF signature parsing and byte-range/integrity validation
- unsigned, valid/unknown trust, locally trusted, modified, invalid and
  unsupported states, including multiple signatures
- explicit local trust store only; no network or implicit OS roots
- OpenCV QR detection/decoding, redacted summary/digest, structure, visible
  consistency, expected geometry and compositing indicators
- cryptographic QR verification remains unsupported unless a profile-specific
  issuer specification is locally implemented
- conservative PDF/image metadata, timestamps, XMP conflict, revision, font,
  compression, EXIF and software indicators
- profile rules for fixed text, regex, numeric ranges, sums, percentages,
  dates/ages, cross-page equality, QR equality, grades and status
- low OCR confidence skips rules and lowers coverage without raising risk

### Handwriting and signature appearance

- 1-5 handwriting exemplars and 2-5 signature exemplars
- profile-first regions, bounded user regions when a profile region is absent,
  then automatic suggestions
- multiple pages/regions
- conservative scale, translation and small-capture-rotation normalization
- HOG/gradient, contour Hu, skeleton, ORB keypoint, texture, projection,
  component, slant and baseline features
- closest exemplar, region evidence, confidence, coverage, reasons and limits
- signature placement/scale and pasted-region boundary/background/noise evidence
  reported separately from author-appearance similarity
- no legal identity, handwriting-expert or definitive authorship claim

### Frontend

- three explicit paths: issued original, official template and closest trusted
  profile
- candidate-only DocuVault upload
- collapsed optional profile override, handwriting and signature inputs
- backend-driven labels for every new stage
- selected profile, top matches and component score explanations
- digital signature, QR, metadata, logical, handwriting, signature and unified
  assessment sections behind progressive disclosure
- candidate-only document viewer when no profile visual reference exists, so
  the internal lifecycle proxy is never misrepresented as a trusted original
- approved professional white/red/limited-black visual system retained

## DocuVault transfer provenance

The inspected source was `E:/Hackathon/Saveetha/docuvault-3060` on 2026-08-29.
It was a detached 145-file text export with no `.git`, so no source branch,
commit or tree exists. Its deterministic aggregate fingerprint is:

`15ece9ac94b5d40aaf721275c1c2ecca22b9aac599cc6675bfdc26b171e619d2`

Reused concepts were narrow and recorded in `backend/docuvault/README.md`: safe
paths, P/A/T trust separation, strict schema strategy, deterministic catalog
state and family/source metadata. No nested repository, official document bytes,
personal records, model, database, cache, secret, key or absolute personal path
was imported. The detached export had no repository-level software licence, so
it was not copied as an opaque package.

## Candidate validation

The consolidated gate completed on 2026-08-29. The initial run exposed one
stale event-stage expectation; the integrated smoke then exposed a Windows
SQLite-handle cleanup defect. Only those subsets were repaired and rerun before
one final complete regression:

- backend: 120 passed in 50.23 seconds;
- frontend: 23 passed across 2 files in 4.29 seconds;
- TypeScript typecheck: PASS;
- production build: PASS, 435 modules in 1.95 seconds;
- integrated core smoke: PASS;
- isolated test/application runtime cleanup: PASS.

The smoke recorded Exact clean/tampered risk `0.0/84.6`, Template
legitimate/manipulated risk `8.1/72.0`, a strong DocuVault score of `79.2`, an
explicit closest-profile fallback, QR mismatch with payload redaction, unknown
and locally trusted signatures, signed-content modification, two failed logical
rules, handwriting separation `79.9/53.6`, signature separation `99.7/32.2`,
and pasted-signature compositing `66.4`.

No RTX 5060 validation or performance benchmark was run for this candidate.

## Confirmed RTX 5060 Phase 1 portability evidence

A separate laptop smoke used Python 3.12.10 and passed the released Phase 1
baseline:

- 34 backend tests passed
- 8 frontend tests passed
- production build passed
- clean/tampered risk: `0` / `93.6`
- localization IoU: `0.2201`
- observed processing: approximately `0.4-0.6 s`
- repository remained clean
- hardware: RTX 5060 with 8 GB VRAM
- driver-reported CUDA compatibility: 13.3
- no system CUDA toolkit installed
- execution remained CPU/OpenCV/PyMuPDF

That is Phase 1 portability evidence only. It does not constitute RTX 5060
acceptance for OCR, multi-page, DocuVault or the new forensic checks.

## Privacy and safety state

- No real personal identity document is part of the repository.
- No private key or credential is committed.
- Signature tests and the integrated smoke generate ephemeral fictional keys.
- No raw QR payload or sensitive logical value is written to logs/results.
- Runtime files, OCR output, screenshots, caches, SQLite files and evidence
  assets remain ignored.
- No system CUDA, driver, global Python or recoverable environment is changed.

## Known limitations

- Official-source profiles contain metadata and structural descriptors, not
  redistributed official visual documents.
- Coverage is not universal across issuer/year/region/language variants.
- A profile match cannot prove issuance or validate personal field values.
- OpenCV QR is the currently supported decoder; other barcodes may be
  unsupported.
- QR cryptographic verification requires an issuer-specific local
  specification and keys.
- Unknown signer trust differs from invalid cryptography; unsigned differs from
  forged.
- Metadata usually cannot identify a specific editing website or person.
- OCR, correspondence and auto-selected handwriting/signature regions remain
  quality-dependent.
- Classical handwriting/signature features are investigative aids, not legal
  identity proof.
- Multiple backend processes cannot share one runtime directory.
- Accounts, cloud/public APIs, custom large-model training, H100 work and
  blockchain/DocuLedger are excluded.

## Local operation

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\diagnose-windows.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap-windows.ps1 -PythonVersion 3.12 -OcrProvider auto -OcrDevice cpu
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-local.ps1
```

Default URLs are frontend `http://127.0.0.1:5173`, backend
`http://127.0.0.1:8000`, health `/api/v1/health`, diagnostics
`/api/v1/diagnostics`, and API docs `/api/docs`.

## Next action

Perform the final manual showcase review of `candidate/phase-3-core-rc1`. Do
not merge to `main` or create a release tag during review.
