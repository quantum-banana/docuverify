# Integrated DocuVault profile data

This directory contains DocuVerify's versioned, privacy-safe profile manifests,
schema and an empty explicit certificate trust-store boundary. Runtime uploads,
OCR text, decoded payloads, profile state, fingerprints and caches are stored in
the ignored runtime directory, never here.

## Detached R1 import provenance

The import source was `E:/Hackathon/Saveetha/docuvault-3060`, inspected on
2026-08-29. It was a detached export with no `.git` directory, so a source
branch, commit and tree SHA do not exist. Its deterministic 145-file content
fingerprint was:

`15ece9ac94b5d40aaf721275c1c2ecca22b9aac599cc6675bfdc26b171e619d2`

The export contained only JSON, Markdown, Python and text files. It had no
official document bytes, personal records, models, databases, caches, secrets,
private keys or absolute personal paths. It also had no repository-level
software licence file, so code was narrowly adapted rather than copied as an
opaque package.

| Integrated component | Detached source | DocuVerify destination |
| --- | --- | --- |
| Forward-slash/no-link path policy | `tools/docuvault_tools/paths.py` | `backend/app/docuvault/safe_paths.py` |
| Conservative P/A/T trust separation | `tools/docuvault_tools/trust.py`, `config/trust-derivation.v1.json` | `backend/app/docuvault/trust.py` |
| Strict Draft 2020-12 schema approach | `tools/docuvault_tools/schema.py`, `schemas/v1/` | `backend/docuvault/schemas/profile.v1.schema.json`, repository validation |
| Deterministic index and duplicate concepts | `tools/docuvault_tools/catalog.py` | `backend/app/docuvault/repository.py` |
| Family and official-source coverage | 30-family catalog and 24 metadata-only records | 19 supported family profiles plus one fictional showcase profile |

No `.git`, nested repository, virtual environment, cache, download, source
artifact, model, database, log or generated file was imported.

## Evidence boundary

Nineteen bundled profiles are `P2` official-source-metadata/generic profiles.
They do not claim a stored official visual original and cannot verify personal
values or issuance. Their explicit capability is `metadata_only`, including the
original Aadhaar-style profile: configured feature locations describe
expectations but are not a stored official specimen.

The separate fictional visual library contains 20 `P0` synthetic demonstration
profiles across the same 19 internal families. It retains the existing Lumen
Grove profile and adds 19 clearly fictional companion profiles, so the complete
catalog has 39 profiles: 19 metadata-only and 20 visual-reference profiles.
This separation prevents synthetic art from silently becoming the visual
original for an official-source metadata profile. Each result exposes
provenance, completeness, component scores, trust tier and limitations; a high
similarity score cannot upgrade weak provenance into issuer proof.

Runtime matching uses issuer text, stable headings, layout anchors, page
geometry, fixed visual evidence where available, expected security regions,
script and completeness. Filenames are excluded. The top three matches are
returned with explanations, and a nearest-but-weak result is explicitly marked
as `Closest available profile`.

## Capability and visual-asset boundary

Every profile declares exactly one capability tier:

| Tier | Permitted evidence |
| --- | --- |
| `metadata_only` | Classification, field formats and expected-feature descriptions |
| `structural` | Metadata plus page geometry, anchors and region occupancy |
| `visual_reference` | Structural checks plus fixed-region comparison with a hash-bound specimen |
| `cryptographic` | The preceding configured evidence plus supported issuer cryptography |

An asset never silently upgrades its parent profile. `reference_assets` binds a
profile, exemplar, document page and source page to a safe local path, MIME
type, byte hash, source dimensions, visual-source class, issuer, profile
version, languages, creation/import method, provenance, redistribution status,
trust level, normalized regions, exact pixel masks, a thumbnail and a complete
v2 visual fingerprint. The fingerprint is source- and mask-bound and includes
fixed-region and edge perceptual hashes, compact layout/colour evidence, border
and anchor geometry, and security-region geometry. The repository recomputes
and verifies every artifact before indexing it.

Each fictional profile has `reference-a` and `reference-b`: the fixed template
is identical while personal values, identifiers, dates, photos, QR payloads and
signatures vary. Matching aligns every page of both exemplars, scores fixed
regions, permits variable identity, uses variable appearance only to select the
most compatible exemplar, and reports the selected exemplar, alignment and
coverage separately from tampering risk. A synthetic visual can influence risk
only for a candidate carrying both the controlled fictional issuer and the
synthetic-demonstration marker. Otherwise it is excluded from pixel risk.

Bundled assets are restricted to `samples/synthetic/`,
`backend/docuvault/references/` or
`backend/docuvault/assets/synthetic/` and must be redistributable. A configured
external vault may reference only its own content-addressed `references/`,
`thumbnails/`, `masks/` and `fingerprints/` directories. Links, reparse points,
traversal, hash mismatches and files over the configured limits are rejected.

## Fictional library and evaluation data

The deterministic generator owns only the tracked synthetic asset and
evaluation roots. It never writes into application runtime storage and never
uses a personal or official document:

```powershell
python -m backend.scripts.generate_docuvault_visual_library
python -m backend.scripts.validate_docuvault_visual_library
```

Trusted visual assets live below `backend/docuvault/assets/synthetic/`.
Tampering fixtures and human-readable ground truth live separately below
`samples/docuvault-visual-evaluation/`; production modules neither import nor
read that ground truth. Every semantic document type has two clean fictional
references and five deterministic, document-appropriate questioned documents.

The semantic-to-profile mapping is explicit; the college marksheet and CGPA
certificate intentionally share the existing
`academic.grade_or_cgpa_certificate` internal family:

| Semantic visual type | Existing control profile | Fictional visual profile |
| --- | --- | --- |
| Class 10 marksheet | `in.cbse.class10.generic.v1` | `synthetic.docuverify.cbse-class10.v1` |
| Class 12 marksheet | `in.cbse.class12.generic.v1` | `synthetic.docuverify.cbse-class12.v1` |
| University marksheet | `generic.university.grade-cgpa.v1` | `synthetic.docuverify.university-marksheet.v1` |
| CGPA certificate | `synthetic.lumen-grove.achievement-record.v1` | same retained Lumen profile |
| Degree certificate | `generic.university.degree.v1` | `synthetic.docuverify.degree-certificate.v1` |
| Aadhaar-style identity | `in.uidai.aadhaar-style.v1` | `synthetic.docuverify.aadhaar-style.v1` |
| Voter-card style | `in.eci.voter-card.v1` | `synthetic.docuverify.voter-card.v1` |
| Ration-card style | `in.nfsa.ration-card.generic.v1` | `synthetic.docuverify.ration-card.v1` |
| University identity | `generic.university.student-id.v1` | `synthetic.docuverify.university-id.v1` |
| Driving-licence style | `in.morth.driving-licence.generic.v1` | `synthetic.docuverify.driving-licence.v1` |
| Passport style | `in.mea.passport.generic.v1` | `synthetic.docuverify.passport.v1` |
| PAN style | `in.itd.pan-card.v1` | `synthetic.docuverify.pan-style.v1` |
| Fee receipt | `generic.education.fee-receipt.v1` | `synthetic.docuverify.fee-receipt.v1` |
| Internship certificate | `generic.education.internship-certificate.v1` | `synthetic.docuverify.internship-certificate.v1` |
| Bonafide certificate | `generic.education.bonafide-certificate.v1` | `synthetic.docuverify.bonafide-certificate.v1` |
| NOC certificate | `generic.education.noc-certificate.v1` | `synthetic.docuverify.noc-certificate.v1` |
| Birth certificate | `in.civil.birth-certificate.generic.v1` | `synthetic.docuverify.birth-certificate.v1` |
| Death certificate | `in.civil.death-certificate.generic.v1` | `synthetic.docuverify.death-certificate.v1` |
| Proof of address | `generic.civil.proof-of-address.v1` | `synthetic.docuverify.proof-of-address.v1` |
| Visa style | `in.mha.visa-document.v1` | `synthetic.docuverify.visa-style.v1` |

## Importing an authorized local specimen

The importer creates content-addressed page assets, WebP thumbnails, binary
masks and v2 fingerprints, then publishes the profile manifest last. It accepts
PDF, PNG and JPEG; a PDF imports all pages unless `--asset-page-number` selects
one. It refuses application job/upload sources, requires a distinct profile ID
when copying a bundled definition, requires explicit authorization and a
capability-upgrade acknowledgement, and never raises the profile provenance.
This prevents a questioned upload from being promoted into trusted reference
material by accident.

```powershell
python -m backend.scripts.import_docuvault_reference `
  --action import `
  --vault-root D:/local-docuvault `
  --profile-manifest backend/docuvault/profiles/core.profile.json `
  --profile-id generic.university.grade-cgpa.v1 `
  --output-profile-id local.example.university-grade.v1 `
  --asset D:/authorized-sources/fictional-university-template.pdf `
  --exemplar-id authorized-2026-en-a `
  --mime-type application/pdf `
  --document-page-number 1 `
  --side front `
  --source-class user_registered_trusted_reference `
  --retrieval-date 2026-08-29 `
  --redistribution-status not_permitted `
  --trust-level P2 `
  --profile-version 2026-en `
  --languages en `
  --creation-method "Authorized local registration" `
  --licence-status-note "Local use only; do not redistribute" `
  --may-influence-tampering-risk `
  --authorized-trusted-reference `
  --authorize-tier-upgrade `
  --confirm-profile-regions
```

Use only a fictional or genuinely authorized specimen whose issuer/layout
matches the selected profile. Do not use a questioned upload or a personal
Aadhaar document. Replace `--confirm-profile-regions` with
`--regions-json <file>` when the inherited normalized regions are not correct.
Re-running an identical import is idempotent; a different update to an existing
external profile requires `--update-existing`.

Lifecycle changes identify an exact exemplar/page/side:

```powershell
python -m backend.scripts.import_docuvault_reference `
  --action disable `
  --vault-root D:/local-docuvault `
  --profile-manifest D:/local-docuvault/profiles/local.example.university-grade.v1.profile.json `
  --profile-id local.example.university-grade.v1 `
  --exemplar-id authorized-2026-en-a `
  --document-page-number 1 `
  --side front
```

Disabling the last active asset is rejected. Removing the last asset requires
`--action remove --authorize-capability-downgrade` and changes the profile to
`metadata_only`. Content-addressed files are deliberately retained so removal
is recoverable and cannot break another record sharing the same digest.
