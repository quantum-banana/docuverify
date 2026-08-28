from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path

import cv2
import fitz
import pytest

from backend.app.docuvault.repository import ProfileRepository
from backend.scripts import import_docuvault_reference as importer
from backend.scripts.import_docuvault_reference import (
    ImportRequest,
    ReferenceMutationRequest,
    disable_reference,
    enable_reference,
    import_reference,
    remove_reference,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = PROJECT_ROOT / "backend" / "docuvault" / "schemas" / "profile.v1.schema.json"
CATALOG = PROJECT_ROOT / "backend" / "docuvault" / "profiles" / "core.profile.json"
REFERENCE = PROJECT_ROOT / "samples" / "synthetic" / "template_reference.pdf"
ALTERNATE = PROJECT_ROOT / "samples" / "synthetic" / "template_manipulated_candidate.pdf"
REFERENCE_PNG = PROJECT_ROOT / "samples" / "synthetic" / "template_reference_page_1.png"
PROFILE_ID = "synthetic.importer-base.v1"
OUTPUT_PROFILE_ID = "local.synthetic.importer-visual.v1"


def _source_profile() -> dict:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    profile = next(
        item for item in catalog if item["profile_id"] == "in.uidai.aadhaar-style.v1"
    )
    profile["profile_id"] = PROFILE_ID
    profile["display_name"] = "Fictional authorized-import fixture"
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
        "licence": "Fictional isolated test fixture.",
    }
    profile["provenance"] = {
        "kind": "generic_family",
        "assurance": "P2",
        "description": "Isolated fixture for an explicitly registered local reference.",
    }
    profile["expected_pages"]["maximum"] = 3
    profile["capability_tier"] = "metadata_only"
    profile["reference_assets"] = []
    if "visual_reference" in profile:
        profile["visual_reference"] = None
    return profile


def _manifest(tmp_path: Path) -> Path:
    manifest = tmp_path / "synthetic-source.profile.json"
    manifest.write_text(json.dumps(_source_profile()), encoding="utf-8")
    return manifest


def _request(tmp_path: Path, **overrides: object) -> ImportRequest:
    values: dict[str, object] = {
        "vault_root": tmp_path / "external-vault",
        "profile_manifest": _manifest(tmp_path),
        "profile_id": PROFILE_ID,
        "output_profile_id": OUTPUT_PROFILE_ID,
        "asset_path": REFERENCE,
        "exemplar_id": "reference-a",
        "document_page_number": 1,
        "asset_page_number": None,
        "side": "front",
        "mime_type": "application/pdf",
        "source_class": "user_registered_trusted_reference",
        "source_url": None,
        "retrieval_date": "2026-08-29",
        "redistribution_status": "not_permitted",
        "trust_level": "P2",
        "profile_version": "test-2026",
        "languages": ("en",),
        "creation_method": "Authorized local test import.",
        "licence_status_note": "Fictional test asset; local evaluation only.",
        "may_influence_tampering_risk": True,
        "demonstration_only": False,
        "authorized_trusted_reference": True,
        "authorize_tier_upgrade": True,
        "confirm_profile_regions": True,
        "regions_json": None,
        "schema_path": SCHEMA,
    }
    values.update(overrides)
    return ImportRequest(**values)  # type: ignore[arg-type]


def _repository(tmp_path: Path, vault_root: Path) -> ProfileRepository:
    return ProfileRepository(
        bundled_root=PROJECT_ROOT / "backend" / "docuvault" / "profiles",
        schema_path=SCHEMA,
        index_path=tmp_path / "profile-index.sqlite3",
        project_root=PROJECT_ROOT,
        external_root=vault_root,
    )


def _two_page_pdf(tmp_path: Path) -> Path:
    output = tmp_path / "two-page-reference.pdf"
    with fitz.open(REFERENCE) as source, fitz.open() as document:
        document.insert_pdf(source)
        document.insert_pdf(source)
        document.save(output)
    return output


def _two_page_regions(tmp_path: Path) -> Path:
    profile = _source_profile()

    def for_both_pages(values: list[dict]) -> list[dict]:
        result: list[dict] = []
        for page in (1, 2):
            for value in values:
                copied = json.loads(json.dumps(value))
                copied["page"] = page
                copied["region_id"] = f"p{page}.{copied['region_id']}"
                result.append(copied)
        return result

    payload = {
        "regions": {
            "fixed": for_both_pages(profile["regions"]["fixed"]),
            "variable": for_both_pages(profile["regions"]["variable"]),
        },
        "security_regions": {
            key: for_both_pages(values)
            for key, values in profile["security_regions"].items()
        },
    }
    path = tmp_path / "confirmed-regions.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_import_is_content_addressed_complete_atomic_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _request(tmp_path)
    writes: list[Path] = []
    atomic_write = importer._atomic_write

    def recording_write(path: Path, content: bytes) -> None:
        atomic_write(path, content)
        writes.append(path)

    monkeypatch.setattr(importer, "_atomic_write", recording_write)
    first = import_reference(request)
    second = import_reference(request)

    assert first.asset_created is True
    assert first.assets_created == 1
    assert first.profile_changed is True
    assert first.asset_path.suffix == ".png"
    assert first.asset_path.stem == first.asset_sha256
    assert first.asset_sha256 == hashlib.sha256(first.asset_path.read_bytes()).hexdigest()
    assert second.asset_created is False
    assert second.profile_changed is False
    assert writes[-1] == first.profile_path

    manifest = json.loads(first.profile_path.read_text(encoding="utf-8"))
    asset = manifest["reference_assets"][0]
    assert asset["exemplar_id"] == "reference-a"
    assert asset["source_class"] == "user_registered_trusted_reference"
    assert asset["profile_version"] == "test-2026"
    assert asset["languages"] == ["en"]
    assert asset["mime_type"] == "image/png"
    assert asset["asset_page_number"] == 1
    assert asset["thumbnail"]["mime_type"] == "image/webp"
    assert asset["thumbnail"]["relative_path"].endswith(".webp")
    assert asset["precomputed_fingerprint"]["algorithm"] == "docuverify-visual-fingerprint-v2"
    for descriptor in (
        asset["thumbnail"],
        *asset["pixel_masks"].values(),
        asset["fingerprint_file"],
    ):
        assert (request.vault_root / descriptor["relative_path"]).is_file()

    repository = _repository(tmp_path, request.vault_root)
    repository.startup()
    imported = repository.get(first.profile_id)
    assert imported is not None
    assert imported.capability_tier == "visual_reference"
    assert imported.reference_exemplars() == ("reference-a",)
    assert imported.reference_assets[0].path == first.asset_path


def test_multi_page_pdf_renders_every_page_with_explicit_regions(tmp_path: Path) -> None:
    request = _request(
        tmp_path,
        asset_path=_two_page_pdf(tmp_path),
        exemplar_id="two-page-reference",
        confirm_profile_regions=False,
        regions_json=_two_page_regions(tmp_path),
    )

    result = import_reference(request)

    assert len(result.asset_paths) == 2
    assert result.assets_created == 1  # Identical rendered pages share content-addressed bytes.
    assert all(path.suffix == ".png" for path in result.asset_paths)
    manifest = json.loads(result.profile_path.read_text(encoding="utf-8"))
    assert [asset["document_page_number"] for asset in manifest["reference_assets"]] == [1, 2]
    assert all(asset["asset_page_number"] == 1 for asset in manifest["reference_assets"])
    assert "Source PDF page 2" in manifest["reference_assets"][1]["creation_method"]


@pytest.mark.parametrize(
    ("mime_type", "suffix"),
    (("image/png", ".png"), ("image/jpeg", ".jpg")),
)
def test_raster_png_and_jpeg_are_supported(
    tmp_path: Path, mime_type: str, suffix: str
) -> None:
    if mime_type == "image/png":
        source = REFERENCE_PNG
    else:
        source = tmp_path / "reference.jpg"
        image = cv2.imread(str(REFERENCE_PNG), cv2.IMREAD_COLOR)
        assert image is not None
        success, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 92])
        assert success
        source.write_bytes(encoded.tobytes())
    request = _request(
        tmp_path,
        asset_path=source,
        mime_type=mime_type,
        asset_page_number=1,
    )

    result = import_reference(request)

    assert result.asset_path.suffix == suffix
    manifest = json.loads(result.profile_path.read_text(encoding="utf-8"))
    assert manifest["reference_assets"][0]["mime_type"] == mime_type


def test_multiple_exemplars_are_preserved_but_duplicates_and_conflicts_reject(
    tmp_path: Path,
) -> None:
    first = _request(tmp_path)
    imported = import_reference(first)
    second = replace(
        first,
        asset_path=ALTERNATE,
        exemplar_id="reference-b",
        update_existing=True,
    )
    import_reference(second)
    manifest = json.loads(imported.profile_path.read_text(encoding="utf-8"))
    assert [asset["exemplar_id"] for asset in manifest["reference_assets"]] == [
        "reference-a",
        "reference-b",
    ]

    with pytest.raises(ValueError, match="duplicate specimen"):
        import_reference(
            replace(first, exemplar_id="reference-c", update_existing=True)
        )
    with pytest.raises(ValueError, match="conflicting specimen"):
        import_reference(
            replace(first, asset_path=ALTERNATE, update_existing=True)
        )


def test_import_requires_authorization_regions_tier_upgrade_and_conservative_trust(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    with pytest.raises(ValueError, match="explicit confirmation"):
        import_reference(replace(request, authorized_trusted_reference=False))
    with pytest.raises(ValueError, match="choose exactly one"):
        import_reference(replace(request, confirm_profile_regions=False))
    with pytest.raises(ValueError, match="authorize-tier-upgrade"):
        import_reference(replace(request, authorize_tier_upgrade=False))
    with pytest.raises(ValueError, match="conservative cap"):
        import_reference(
            replace(
                request,
                source_class="authorized_official_specimen",
                trust_level="P4",
            )
        )
    with pytest.raises(ValueError, match="distinct --output-profile-id"):
        import_reference(replace(request, output_profile_id=request.profile_id))


def test_runtime_vault_is_allowed_but_job_upload_sources_are_rejected(tmp_path: Path) -> None:
    vault = tmp_path / "backend" / "runtime" / "docuvault"
    request = _request(tmp_path, vault_root=vault)
    result = import_reference(request)
    assert result.profile_path.is_relative_to(vault)

    jobs = tmp_path / "backend" / "runtime" / "jobs" / "job-1"
    jobs.mkdir(parents=True)
    questioned = jobs / "questioned-upload.pdf"
    shutil.copyfile(REFERENCE, questioned)
    with pytest.raises(ValueError, match="job/upload runtime"):
        import_reference(replace(request, asset_path=questioned))


def test_enable_disable_remove_keeps_index_valid_and_downgrades_last_removal(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    imported = import_reference(request)
    import_reference(
        replace(
            request,
            asset_path=ALTERNATE,
            exemplar_id="reference-b",
            update_existing=True,
        )
    )

    first = ReferenceMutationRequest(
        vault_root=request.vault_root,
        profile_manifest=imported.profile_path,
        profile_id=OUTPUT_PROFILE_ID,
        exemplar_id="reference-a",
        document_page_number=1,
        side="front",
        schema_path=SCHEMA,
    )
    assert disable_reference(first).action == "disabled"
    assert enable_reference(first).action == "enabled"
    assert remove_reference(first).capability_tier == "visual_reference"

    last = replace(first, exemplar_id="reference-b")
    with pytest.raises(ValueError, match="last active visual asset cannot be disabled"):
        disable_reference(last)
    with pytest.raises(ValueError, match="capability-downgrade authorization"):
        remove_reference(last)
    removed = remove_reference(
        replace(last, authorize_capability_downgrade=True)
    )
    assert removed.capability_tier == "metadata_only"

    repository = _repository(tmp_path, request.vault_root)
    repository.startup()
    profile = repository.get(OUTPUT_PROFILE_ID)
    assert profile is not None
    assert profile.capability_tier == "metadata_only"
    assert profile.reference_assets == ()
