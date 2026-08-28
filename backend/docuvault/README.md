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
Aadhaar-style profile: configured feature locations describe expectations but
are not a stored official specimen. The Lumen Grove profile and its hash-bound
existing reference are fictional showcase material (`P0`) with the explicit
`visual_reference` capability. The 20 validated profiles cover 19 document
families. Each result exposes provenance, completeness, component scores, trust
tier and limitations; a high similarity score cannot upgrade weak provenance
into issuer proof.

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
profile/page/side to a safe local path, MIME type, byte hash, source dimensions,
source and redistribution metadata, trust level, fixed and variable masks,
security-element regions, and a fixed-mask fingerprint. The repository verifies
the media signature, SHA-256, dimensions, mask binding and fingerprint before it
indexes the profile. Matcher scoring excludes the visual component entirely
when the declared tier or a verified asset is absent; it does not substitute a
neutral-looking fake score.

Bundled assets are restricted to `samples/synthetic/` or
`backend/docuvault/references/` and must be redistributable. A configured
external vault may reference only its own `references/` directory. Links,
reparse points, traversal and absolute paths are rejected.

## Importing an authorized local specimen

The importer creates a content-addressed asset and an atomically written profile
inside an external vault. It refuses application `runtime` paths, requires a
distinct profile ID when copying a bundled definition, and requires an explicit
capability-upgrade acknowledgement. This prevents a questioned upload from
being promoted into trusted reference material by accident.

```powershell
python -m backend.scripts.import_docuvault_reference `
  --vault-root D:/authorized-docuvault `
  --profile-manifest backend/docuvault/profiles/core.profile.json `
  --profile-id in.uidai.aadhaar-style.v1 `
  --output-profile-id local.uidai.aadhaar-authorized.v1 `
  --asset D:/approved-sources/aadhaar-specimen.pdf `
  --mime-type application/pdf `
  --page-number 1 `
  --side front `
  --source-url https://example.invalid/replace-with-authoritative-source `
  --retrieval-date 2026-08-29 `
  --redistribution-status not_permitted `
  --trust-level P2 `
  --authorized-trusted-reference `
  --authorize-tier-upgrade
```

The example URL is a placeholder and must be replaced with the real authorized
source. The importer does not claim official status, raise provenance, or add
issuer cryptographic keys. Re-running an identical import is idempotent; a
different update to an existing external profile requires `--update-existing`.
