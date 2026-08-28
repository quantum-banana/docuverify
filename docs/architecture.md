# DocuVerify core architecture

## Local trust boundary

DocuVerify is a two-process local application. The React/TypeScript frontend is
a presentation client. FastAPI is authoritative for validation, private
storage, rendering, OCR, profile retrieval, forensic checks, scoring, progress,
results and allowlisted assets. The browser never receives a filesystem path or
parses an uploaded PDF.

The application produces investigative evidence, not an authenticity
probability. It keeps these dimensions separate:

- visual tampering risk;
- trusted-reference/profile strength;
- profile-match score;
- digital PDF-signature state;
- QR/barcode consistency and cryptographic availability;
- metadata/provenance indicators;
- logical field consistency;
- OCR confidence and analysis coverage;
- handwriting appearance similarity;
- signature appearance similarity and independent compositing evidence.

## Main components

| Area | Responsibility |
| --- | --- |
| `frontend/src/api` | Versioned API calls, candidate-only/reference submission, SSE recovery and defensive normalization |
| `frontend/src/types` | Stable UI contracts for pages, profiles and independent evidence dimensions |
| `frontend/src/components` | Safe rendered viewers, page markers/navigation and evidence drawer |
| `backend/app/api` | Health, diagnostics, profile catalog/state, analysis, SSE and registered assets |
| `backend/app/models` | Canonical additive Pydantic contracts |
| `backend/app/core` | Environment settings and private SQLite/runtime storage |
| `backend/app/docuvault` | Safe paths, strict repository/index, trust tiers and multi-signal matching |
| `backend/app/services/pipeline.py` | Bounded orchestration, progress, visual findings and unified result |
| `backend/app/services/digital_signatures.py` | pyHanko signature/incremental-revision inspection with explicit local trust |
| `backend/app/services/qr_codes.py` | Local QR provider abstraction, redacted parsing and visible consistency |
| `backend/app/services/metadata_forensics.py` | Conservative PDF/image metadata and revision indicators |
| `backend/app/services/logical_rules.py` | Versioned profile-driven field rules and low-OCR skip behavior |
| `backend/app/services/biometric_similarity.py` | Classical handwriting/signature ensemble and signature compositing evidence |
| `backend/app/services/assessment.py` | Deterministic multidimensional investigative assessment |
| `backend/app/forensics` | Alignment, difference localization, template roles and visual scoring |
| `backend/docuvault` | Versioned profiles/schema, empty trust-store boundary and provenance notes |
| `samples` | Deterministic fictional inputs and test-only expected masks/manifests |

## Input modes and limits

`POST /api/v1/analyses/reference` accepts `exact` or `template` plus a trusted
reference and questioned candidate. `POST /api/v1/analyses/automatic` accepts a
candidate and retrieves a DocuVault profile. Both accept optional 1-5
handwriting exemplars and 2-5 signature exemplars. Automatic mode also accepts
an exact local profile-ID override. Normalized page regions can be supplied to
the API; profile regions take precedence and automatic suggestions are the last
fallback.

Each PDF contains 1-10 physical pages. PNG/JPEG inputs are single-page. A result
may contain up to 20 review slots when unmatched reference and candidate pages
must both be represented. Page work remains sequential and full-size
intermediates are released when no longer required.

## Lifecycle

1. Validate extension, MIME, signature, size, page count and usable content.
2. Store sanitized role-named inputs beneath a generated private job directory.
3. Render/normalize pages and extract embedded text or local raster OCR.
4. In DocuVault mode, identify family signals, search validated profiles, rank
   issuer/layout evidence and optionally replace the internal lifecycle proxy
   with a real stored profile visual reference.
5. Estimate page correspondence and expose missing, added, reordered and
   dimension anomalies.
6. Align matched pages and run Exact or Template visual/text forensics.
7. Decode supported codes, inspect PDF signatures and metadata, and evaluate
   profile logical rules.
8. Compare requested handwriting and signatures against their exemplar
   ensembles; inspect signature placement/scale/compositing independently.
9. Materialize suspicious findings and registered crops/diagnostic overlays.
10. Aggregate page evidence and build the separate investigative dimensions.
11. Persist terminal state and replayable SSE events; polling can recover the
    same result if streaming is interrupted.

Every new progress stage is backend-driven. There are no artificial UI delays.

## DocuVault validation and retrieval

Profile manifests are validated against `profile.v1.schema.json` with Draft
2020-12 semantics, bounded fields, safe relative references and deterministic
fingerprints. Invalid or duplicate profiles are diagnosed and excluded rather
than partially trusted. The SQLite index stores catalog state/fingerprints, not
uploaded document content or OCR text.

Retrieval returns three ranked matches. The weighted components are issuer
text, stable headings, layout anchors, expected page geometry, fixed visual
perceptual evidence, expected security-region evidence, script and completeness.
The filename is never used. Explicit overrides are labelled and do not inflate
their score.

Trust separates provenance from applicability:

- `Issuer cryptographically verified` requires configured cryptographic proof;
- `Trusted exact issued reference` requires an independently trusted exact
  reference;
- `Strong trusted-profile match` requires P2/P3 provenance and a strong match;
- `Moderate trusted-profile match` retains unresolved variant details;
- `Closest available profile` is context only.

No match tier proves personal values or issuance.

## Digital-signature boundary

pyHanko inspects PDF signature fields, signed byte ranges, cryptographic
integrity, post-signing incremental updates, signer certificates, signing time
and multiple signatures. Trust comes only from certificates in the configured
local directory. No AIA/OCSP/CRL/network fetching or implicit operating-system
root set is used.

The result distinguishes cryptographically valid and locally trusted, valid
with unknown trust, modified signed content, invalid/broken, unsigned and
unsupported formats. Unsigned does not mean forged; unknown trust does not mean
invalid.

## QR/code boundary

The active provider uses OpenCV QR detection/decoding. The provider interface
allows another local maintained decoder later without changing the result
contract. The backend records a redacted payload summary and SHA-256 digest,
never raw sensitive payload text in report/log surfaces. Structure, required
keys/prefixes, visible-field equality, expected region, geometry and
compositing indicators remain separate.

Cryptographic verification is `unsupported` unless a profile provides a
specific locally implemented issuer format and verification material. A valid
JSON or visible match is not a cryptographic signature.

## Metadata and logical rules

Metadata inspection can report contradictory creation/modification timelines,
XMP conflicts, revisions/incremental updates, producer/creator/software fields,
embedded-font changes, EXIF, mixed compression/resolution, re-encoding and
missing metadata. It reports evidence and limitations, never an invented
website or editor.

Logical rules are profile-versioned and deterministic. Extracted sensitive
values are redacted before serialization. Each rule exposes the fields used,
status, confidence and explanation. If OCR confidence is below the rule's
minimum, the rule skips and coverage is limited; no risk is created merely by
missing text.

## Handwriting and signature ensemble

Exemplar images or pages are rendered locally and foreground quality is checked.
Blank/weak samples are excluded instead of treated as mismatches. Cropped
samples are scale/translation normalized. Only small capture rotation is
corrected; larger slant is preserved as potential style evidence.

The ensemble combines:

- HOG/gradient direction;
- local texture/ink distribution;
- horizontal/vertical projection and spacing;
- contour Hu moments and curvature;
- connected components, baseline and slant structure;
- morphological skeleton overlap;
- ORB keypoint agreement.

Scores aggregate against multiple trusted exemplars and return closest sample,
confidence, coverage, per-region measurements, reasons and limitations.
Signature regions also produce independent boundary/background/noise
compositing plus profile-region placement/scale indicators. Similarity does not
erase a paste indicator, and neither result is definitive authorship or legal
identity proof.

## Visual evidence and scoring

Existing alignment uses exact identity where possible, ORB/RANSAC homography
when reliable and a bounded geometry fallback. Difference localization combines
pixel intensity, edges, OCR/text boxes, residual content and region-role
reasoning. All finding boxes are normalized candidate-page coordinates.

Template variable content can differ without becoming suspicious. Typography,
stroke weight, baseline, spacing, geometry, local background, residual text,
erasure halo, compression/noise and compositing remain active for every variable
field. Fixed labels stay strict. Raster analysis does not claim an exact font
family.

Visual page aggregation keeps the maximum page evidence plus bounded
corroboration so clean pages cannot erase a strong one. Digital, logical,
metadata and biometric dimensions are not silently converted into the visual
tampering score; the unified assessment displays contradictions and coverage
explicitly.

## Frontend presentation

The upload surface preserves the approved professional white/red/limited-black
system. Advanced exemplar/profile inputs are collapsed. Results show the main
visual risk first and independent evidence in compact disclosure sections.

For DocuVault profiles without stored visual bytes, only the questioned viewer
is shown. The candidate-copy proxy used internally to preserve the bounded page
lifecycle is never labelled or displayed as a trusted reference.

## Storage, privacy and portability

Runtime uploads, page renders, OCR output, decoded data, SQLite files, crops,
overlays, caches, logs and trust material remain under ignored local paths.
Asset lookup requires registered `(job_id, asset_id)` pairs. Retention removes
only terminal jobs older than the configured cutoff and tolerates Windows locks
for later retry.

Python 3.12.10 is the tested baseline. CPU raster OCR is mandatory. Driver CUDA
compatibility is diagnostics only and does not imply a toolkit or supported GPU
OCR provider. The build does not install/modify CUDA, drivers, global Python or
recoverable environments.

## Explicit exclusions

Accounts, cloud deployment, public verification APIs, custom large-model
training, H100 processing, legal identity certification and claims that infer a
specific editing website are excluded. Blockchain/DocuLedger remains a final
optional future phase.
