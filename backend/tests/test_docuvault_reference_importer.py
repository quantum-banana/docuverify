from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from backend.app.docuvault.repository import ProfileRepository
from backend.scripts.import_docuvault_reference import ImportRequest, import_reference


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = PROJECT_ROOT / "backend" / "docuvault" / "schemas" / "profile.v1.schema.json"
CATALOG = PROJECT_ROOT / "backend" / "docuvault" / "profiles" / "core.profile.json"
REFERENCE = PROJECT_ROOT / "samples" / "synthetic" / "template_reference.pdf"


def _synthetic_manifest(tmp_path: Path) -> Path:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    profile = next(
        item for item in catalog if item["profile_id"] == "in.uidai.aadhaar-style.v1"
    )
    profile["profile_id"] = "synthetic.importer-base.v1"
    profile["issuer"] = {
        "id": "synthetic.importer",
        "name": "Synthetic Importer Fixture",
        "aliases": ["Synthetic fixture"],
    }
    profile["source"] = {
        "record_id": None,
        "authoritative_url": None,
        "retrieved_at": "2026-08-29",
        "sha256": None,
        "format": "synthetic",
        "redistribution_status": "permitted",
        "licence": "Fictional test fixture.",
    }
    profile["provenance"] = {
        "kind": "synthetic_showcase",
        "assurance": "P0",
        "description": "Fictional importer test profile.",
    }
    manifest = tmp_path / "synthetic-source.profile.json"
    manifest.write_text(json.dumps(profile), encoding="utf-8")
    return manifest


def _request(tmp_path: Path) -> ImportRequest:
    return ImportRequest(
        vault_root=tmp_path / "external-vault",
        profile_manifest=_synthetic_manifest(tmp_path),
        profile_id="synthetic.importer-base.v1",
        output_profile_id="synthetic.importer-visual.v1",
        asset_path=REFERENCE,
        page_number=1,
        side="front",
        mime_type="application/pdf",
        source_url=None,
        retrieval_date="2026-08-29",
        redistribution_status="not_permitted",
        trust_level="P0",
        authorized_trusted_reference=True,
        authorize_tier_upgrade=True,
        schema_path=SCHEMA,
    )


def test_authorized_import_is_content_addressed_atomic_and_idempotent(tmp_path: Path) -> None:
    request = _request(tmp_path)

    first = import_reference(request)
    second = import_reference(request)

    assert first.asset_created is True
    assert first.profile_changed is True
    assert first.asset_path.name == f"{first.asset_sha256}.pdf"
    assert second.asset_created is False
    assert second.profile_changed is False
    repository = ProfileRepository(
        bundled_root=PROJECT_ROOT / "backend" / "docuvault" / "profiles",
        schema_path=SCHEMA,
        index_path=tmp_path / "profile-index.sqlite3",
        project_root=PROJECT_ROOT,
        external_root=request.vault_root,
    )
    repository.startup()
    imported = repository.get(first.profile_id)
    assert imported is not None
    assert imported.capability_tier == "visual_reference"
    assert imported.visual_reference_path == first.asset_path
    assert imported.reference_assets[0].redistribution_status == "not_permitted"


def test_importer_requires_explicit_tier_upgrade_and_distinct_external_id(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    with pytest.raises(ValueError, match="authorize-tier-upgrade"):
        import_reference(replace(request, authorize_tier_upgrade=False))
    with pytest.raises(ValueError, match="explicit confirmation"):
        import_reference(replace(request, authorized_trusted_reference=False))
    with pytest.raises(ValueError, match="distinct --output-profile-id"):
        import_reference(replace(request, output_profile_id=request.profile_id))


def test_importer_rejects_application_runtime_inputs(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    runtime_asset = runtime / "questioned-upload.pdf"
    shutil.copyfile(REFERENCE, runtime_asset)

    with pytest.raises(ValueError, match="runtime directory"):
        import_reference(replace(_request(tmp_path), asset_path=runtime_asset))
