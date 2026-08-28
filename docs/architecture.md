# Phase 2 architecture

## Boundary and trust model

DocuVerify remains a two-process local application. The React/TypeScript/Vite frontend is a presentation client: it selects files, submits requests, displays backend progress/results, and fetches only registered API assets. It never receives a local filesystem path. PDFs are not passed to a browser PDF parser or local object URL; previews and evidence are backend-rendered browser-safe images.

FastAPI is authoritative for validation, private storage, page rendering, text/OCR extraction, page correspondence, alignment, comparison, scoring, job state, progress, and asset access. The backend, not UI timing, determines stage completion and result values.

Phase 2 is currently an unreleased working-tree implementation on `phase-2-work`, based on Phase 1 commit `ee2ad3ca7defe1010ac1d3f6be39bd5eee205392`. The additive contracts retain Phase 1-compatible defaults. Its verified candidate status is `PASS_ON_4060_PENDING_RTX5060`; this is not final release readiness.

## Main components

| Area | Responsibility |
| --- | --- |
| `frontend/src/api` | Versioned API calls, SSE reconnect/backoff, status polling, and wire-shape normalization |
| `frontend/src/types` | UI-facing exact/template, page, OCR, suggestion, anomaly, aggregate, and progress types |
| `frontend/src/components` | Upload, safe document preview, selected-page markers, filmstrip/navigation, and evidence details |
| `backend/app/models` | Canonical Pydantic job/result, page, finding, OCR, correspondence, aggregate, progress, diagnostics, and error contracts |
| `backend/app/api` | Health, diagnostics, upload/demo, job status, event stream, and registered-asset endpoints |
| `backend/app/core` | Environment settings and private SQLite/runtime storage |
| `backend/app/services/documents.py` | Content validation, 10-page limit, page rendering, embedded text, and raster OCR fallback |
| `backend/app/services/ocr.py` | Cached RapidOCR/ONNX Runtime provider and truthful failure results |
| `backend/app/services/pipeline.py` | Sequential page orchestration, correspondence, findings, evidence, aggregation, and progress |
| `backend/app/forensics` | Alignment, text/role comparison, localized differences, and deterministic scoring |
| `samples/synthetic` | Deterministic fictional Phase 1 and Phase 2 inputs/previews |
| `samples/expected` | Test-only manifests and expected masks; production forensics never reads them |

## Input and resource limits

Each PDF input may contain 1 through 10 physical pages. PNG and JPEG inputs remain single-page. Validation rejects more than 10 physical pages in either upload, encrypted or structurally corrupt PDFs, unsupported formats, invalid image dimensions, and pages that are unusable under the document rules.

The per-upload maximum can be configured downward with `DOCUVERIFY_MAX_PAGES`, but it cannot be raised above 10. Correspondence may produce up to 20 ordered review slots when missing reference pages and added candidate pages must both be represented. Review-slot count is therefore distinct from each input's physical page count. Page analysis is sequential by default to bound CPU/RAM usage and OCR work. The design does not create one OCR engine per page.

## Multi-page lifecycle

1. The API validates both uploads and captures their page counts before accepting a job.
2. The backend stores sanitized role-based inputs beneath a generated private job directory and persists the queued state.
3. The analysis plan emits page-aware progress for physical input work; the eventual ordered correspondence can contain the union of unmatched reference and candidate pages.
4. Pages are rendered and normalized into a stable candidate-oriented coordinate system.
5. Page correspondence is estimated from heading/text similarity, perceptual/layout evidence, dimensions, and page position.
6. Matched pages proceed through text/OCR extraction, alignment, structure/template reasoning, visual localization, scoring, and evidence generation.
7. Unmatched or out-of-order pages produce explicit missing, added, reordered, or dimension-mismatch status/evidence; they are not silently ignored.
8. Per-page risk, confidence, coverage, OCR status, findings, suggestions, and assets are persisted.
9. A deterministic aggregate retains the strongest page evidence so clean pages cannot erase a suspicious page.
10. The final result remains available through status polling after SSE closes.

There are no artificial backend delays. Phase 2 remains a local in-process executor rather than a distributed scheduler.

## Page correspondence and anomalies

Index order is the conservative starting point. Correspondence scoring can identify when another reference page is a materially better match by combining text/headings, perceptual similarity, dimensions, and layout. The resulting mapping distinguishes:

- `matched`: expected candidate/reference relationship;
- `missing`: a trusted reference page has no candidate counterpart;
- `added`: a candidate page has no trusted counterpart;
- `reordered`: content corresponds to a different position;
- `dimension_mismatch`: corresponding pages have incompatible geometry.

Correspondence is heuristic. Near-identical boilerplate pages may remain ambiguous; the result exposes the mapping and anomaly instead of claiming semantic certainty.

## Exact and template comparison

The API accepts `exact` and `template` modes.

### Exact mode

All stable document content is expected to match closely. Page count/order, dimensions, fixed and value text, OCR output, layout, logos/seals, visual differences, inserted/removed regions, and available metadata may contribute evidence.

### Template mode

Changed text receives a role:

- `fixed`: stable structure or label;
- `variable`: a suggested value field allowed to differ;
- `unknown`: insufficient evidence for either role.

Variable suggestions use normalized word boxes and recognizable stable labels, including name, recipient, identifier, date, result, grade, mark, and score patterns. A consistent variable-value change is informational or low risk. Independent typography or compositing evidence can still raise risk. Fixed-region changes receive greater weight.

The heuristic does not claim complete document understanding or exact raster font-family identification.

## OCR and text-provider architecture

Text extraction returns words, normalized boxes, confidence, provider/source, device, coverage, success, and an error state where applicable.

1. Reliable born-digital PDF text uses PyMuPDF embedded extraction.
2. Raster/image-only pages use a cached RapidOCR 3.9.2 engine with ONNX Runtime 1.29.0.
3. Python 3.12 is the tested cross-laptop runtime baseline; Python 3.11 is not supported by the current dependency set, and Python 3.14 is not selected automatically for Phase 2 OCR.
4. The verified device is CPU; GPU detection is diagnostics only, and CPU raster OCR is the mandatory fallback.
5. The provider is initialized once per process/cache key and reused across pages.
6. An OCR exception is contained at the page boundary. Visual comparison continues, OCR success remains false, and coverage is reduced.
7. Missing OCR does not itself increase tampering risk.

`DOCUVERIFY_OCR_PROVIDER=auto` and `DOCUVERIFY_OCR_DEVICE=cpu` are the supported defaults. `none` permits an intentional visual-only setup. GPU OCR may be attempted only through a supported provider/runtime combination; the verified Phase 2 configuration rejects an unsupported GPU request. Driver-reported CUDA compatibility is not an installed toolkit or GPU OCR capability, and no system CUDA toolkit installation or modification belongs to this phase.

## Alignment and finding localization

The alignment stage maps the trusted reference into candidate-page space. It uses exact identity when pixels match, ORB/RANSAC homography when evidence is reliable, and a bounded dimension-aware fallback otherwise.

Difference localization combines adaptive pixel intensity, edges, connected components, and text boxes. Reference and candidate text extents are unioned; reference boxes can be mapped through the actual reference-to-candidate homography. Context padding is based on line height rather than fixture coordinates. Nearby components merge transitively while border noise and weak components are suppressed.

Every finding bounding box is serialized in normalized candidate-page coordinates `(x, y, width, height)` within 0 to 1. Production code does not read expected fixture manifests or masks. Test-only masks measure regression IoU.

Observed scoped regression values are Phase 1 IoU `0.3745511831` and three-page page-2 IoU `0.5584561077`.

## Evidence and scoring separation

Each finding retains page number, category, title, explanation, normalized box, risk, confidence, severity, reference/candidate crops, difference overlay, evidence sources, and supporting measurements.

The system keeps these concepts separate:

- tampering risk: strength of suspicious evidence;
- assessment confidence: reliability of the assessment inputs/alignment;
- analysis coverage: how much requested analysis was actually available;
- alignment quality: trust in geometric correspondence;
- OCR confidence: provider confidence for recognized text.

Weak alignment lowers confidence. OCR failure lowers coverage. Allowed variable-value changes do not dominate risk. Strong fixed-region or compositing evidence can remain high risk. Document aggregation preserves the strongest page evidence and adds only bounded corroboration.

Scores are deterministic investigative indicators, not authenticity probabilities.

## Progress, SSE, and recovery

Persisted progress events include overall progress, current page, total pages, page stage, finding count, OCR provider, and optionally a localized region or candidate page URL. Per-page result summaries record the reference/candidate OCR provider and execution device.

The frontend treats SSE as a notification channel rather than the sole state record. It reconnects with bounded backoff, uses event identifiers for replay, polls job status while disconnected, and fetches terminal results after fast completion. The UI does not invent page completion.

## Frontend page model

The result interface derives its selected page from backend page results and correspondence:

- the filmstrip exposes page order, risk, finding count, and anomaly state;
- previous/next and direct page selection update one selected-page viewer;
- only findings for the selected page render markers;
- selecting a finding navigates to its page before opening evidence;
- missing candidate pages display an explicit unavailable state;
- suggested variable regions are visually distinct from findings;
- normalized coordinates preserve marker alignment across responsive resizing.

The browser receives only registered PNG assets, never raw local file paths.

## Local storage and retention

The configurable runtime root contains SQLite state and generated per-job inputs/assets. Asset lookup uses `(job_id, asset_id)` registrations and cannot nominate arbitrary paths. Runtime data, OCR/model caches, virtual environments, logs, screenshots, uploads, databases, and evidence are ignored by Git.

Retention selects only terminal jobs older than the configured cutoff. Filesystem cleanup occurs before SQLite deletion; a Windows file lock retains the database record for a later retry. Interrupted-job recovery occurs during ASGI lifespan startup, not merely on module import.

Separate backend processes must not share one runtime directory because there is no cross-process job coordination.

## Repository and phase boundary

The Phase 1 commit/tag remain recoverable on `main` and `origin/main`. Phase 2 is uncommitted and unpushed on `phase-2-work`. The final Phase 2 candidate gate passed on the RTX 4060. A separate RTX 5060 Phase 1 smoke passed 34 backend tests, 8 frontend tests, the production build, clean/tampered risk `0/93.6`, and IoU `0.2201` in approximately `0.4-0.6 s`, with a clean repository. That RTX 5060 has 8 GB VRAM and driver-reported CUDA compatibility 13.3 but no system CUDA toolkit; Phase 1 remained CPU/OpenCV/PyMuPDF. Phase 2-specific RTX 5060 OCR and multi-page validation remain pending. Browser validation was unavailable because the browser skill reported zero instances; screenshot paths are `NONE`.

DocuVault remains Phase 3, signature/handwriting work Phase 4, and blockchain Phase 6. Accounts, cloud deployment, public verification APIs, custom training, H100 processing, and unlimited OCR are outside this architecture.
