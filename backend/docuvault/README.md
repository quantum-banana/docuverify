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
values or issuance. The Lumen Grove profile and its hash-bound existing
reference are fictional showcase material (`P0`). The 20 validated profiles
cover 19 document families. Each result exposes provenance, completeness,
component scores, trust tier and limitations; a high similarity score cannot
upgrade weak provenance into issuer proof.

Runtime matching uses issuer text, stable headings, layout anchors, page
geometry, fixed visual evidence where available, expected security regions,
script and completeness. Filenames are excluded. The top three matches are
returned with explanations, and a nearest-but-weak result is explicitly marked
as `Closest available profile`.
