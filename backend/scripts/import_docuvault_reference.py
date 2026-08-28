"""Attach an authorized visual specimen to an external DocuVault profile.

The importer never reads application runtime uploads. Assets are copied into an
external vault by SHA-256, and the resulting profile manifest is validated and
atomically written. A capability-tier upgrade requires an explicit CLI flag.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from backend.app.docuvault.repository import MAX_MANIFEST_BYTES, MAX_REFERENCE_BYTES
from backend.app.docuvault.safe_paths import portable_relative_path, safe_path
from backend.app.docuvault.visual_assets import (
    FINGERPRINT_ALGORITHM,
    fixed_region_fingerprint,
    mask_fingerprint,
    render_visual_page,
    verify_visual_media,
    visual_dimensions,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA = PROJECT_ROOT / "backend" / "docuvault" / "schemas" / "profile.v1.schema.json"
_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,159}$")
_TRUST_RANK = {f"P{number}": number for number in range(5)}
_MIME_SUFFIX = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}


@dataclass(frozen=True, slots=True)
class ImportRequest:
    vault_root: Path
    profile_manifest: Path
    profile_id: str
    output_profile_id: str | None
    asset_path: Path
    page_number: int
    side: str
    mime_type: str
    source_url: str | None
    retrieval_date: str
    redistribution_status: str
    trust_level: str
    authorized_trusted_reference: bool
    authorize_tier_upgrade: bool
    update_existing: bool = False
    schema_path: Path = DEFAULT_SCHEMA


@dataclass(frozen=True, slots=True)
class ImportResult:
    profile_id: str
    profile_path: Path
    asset_path: Path
    asset_sha256: str
    profile_changed: bool
    asset_created: bool


def import_reference(request: ImportRequest) -> ImportResult:
    if not request.authorized_trusted_reference:
        raise ValueError(
            "visual specimens require explicit confirmation that the source is authorized"
        )
    vault_root = request.vault_root.expanduser().resolve(strict=False)
    manifest_input = request.profile_manifest.expanduser()
    source_input = request.asset_path.expanduser()
    if manifest_input.is_symlink() or source_input.is_symlink():
        raise ValueError("profile manifests and visual specimens cannot be links")
    manifest_path = manifest_input.resolve(strict=True)
    source_path = source_input.resolve(strict=True)
    schema_path = request.schema_path.expanduser().resolve(strict=True)
    _reject_runtime_input(manifest_path, "profile manifest")
    _reject_runtime_input(source_path, "visual specimen")
    _reject_runtime_input(vault_root, "external vault")
    if not source_path.is_file():
        raise ValueError("visual specimen must be a regular, non-linked file")
    if source_path.stat().st_size > MAX_REFERENCE_BYTES:
        raise ValueError("visual specimen exceeds the 64 MiB safety limit")
    if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("profile manifest exceeds the 2 MiB safety limit")
    verify_visual_media(source_path, request.mime_type)

    profile = _select_profile(manifest_path, request.profile_id)
    output_profile_id = request.output_profile_id or request.profile_id
    if not _PROFILE_ID.fullmatch(output_profile_id):
        raise ValueError("output profile ID is not portable")

    profiles_root = vault_root / "profiles"
    references_root = vault_root / "references"
    source_is_external = _is_within(manifest_path, profiles_root.resolve(strict=False))
    if not source_is_external and output_profile_id == request.profile_id:
        raise ValueError(
            "importing a bundled profile requires a distinct --output-profile-id; "
            "external profiles cannot silently replace bundled profiles"
        )
    if not source_is_external and profile.get("reference_assets"):
        raise ValueError(
            "a bundled profile that already has assets cannot be copied without importing "
            "each referenced asset into the external vault"
        )

    tier = str(profile.get("capability_tier", ""))
    if tier not in {"visual_reference", "cryptographic"}:
        if not request.authorize_tier_upgrade:
            raise ValueError(
                "attaching a specimen upgrades profile capability; pass "
                "--authorize-tier-upgrade to acknowledge this change"
            )
        profile["capability_tier"] = "visual_reference"

    profile_assurance = str(profile["provenance"]["assurance"])
    if request.trust_level not in _TRUST_RANK:
        raise ValueError("unsupported visual-reference trust level")
    if _TRUST_RANK[request.trust_level] > _TRUST_RANK.get(profile_assurance, 0):
        raise ValueError(
            "asset trust cannot exceed profile provenance without an explicit profile review"
        )

    fixed_masks = _regions_for_page(profile["regions"]["fixed"], request.page_number)
    variable_masks = _regions_for_page(profile["regions"]["variable"], request.page_number)
    if not fixed_masks:
        raise ValueError("the selected profile page has no fixed-region mask")
    security_regions = {
        key: _regions_for_page(values, request.page_number)
        for key, values in profile["security_regions"].items()
    }
    source_bytes = source_path.read_bytes()
    digest = hashlib.sha256(source_bytes).hexdigest()
    dimensions = visual_dimensions(source_path, request.mime_type, request.page_number)
    rendered = render_visual_page(source_path, request.mime_type, request.page_number)
    fingerprint = fixed_region_fingerprint(
        rendered,
        fixed_regions=fixed_masks,
        variable_regions=variable_masks,
        page_number=request.page_number,
    )

    profile["profile_id"] = output_profile_id
    for existing_asset in profile.get("reference_assets", []):
        existing_asset["profile_id"] = output_profile_id
    asset_id = f"reference-p{request.page_number}-{request.side}-{digest[:12]}"
    relative_asset_path = f"references/{digest}{_MIME_SUFFIX[request.mime_type]}"
    asset = {
        "asset_id": asset_id,
        "profile_id": output_profile_id,
        "page_number": request.page_number,
        "side": request.side,
        "relative_path": relative_asset_path,
        "mime_type": request.mime_type,
        "sha256": digest,
        "dimensions": {
            "width": dimensions.width,
            "height": dimensions.height,
            "unit": dimensions.unit,
        },
        "source_url": request.source_url,
        "retrieval_date": request.retrieval_date,
        "redistribution_status": request.redistribution_status,
        "trust_level": request.trust_level,
        "fixed_region_masks": fixed_masks,
        "variable_region_masks": variable_masks,
        "security_element_regions": security_regions,
        "precomputed_fingerprint": {
            "algorithm": FINGERPRINT_ALGORITHM,
            "value": fingerprint,
            "mask_sha256": mask_fingerprint(fixed_masks, variable_masks),
        },
    }
    assets = [
        item
        for item in profile.get("reference_assets", [])
        if not (
            int(item["page_number"]) == request.page_number
            and str(item["side"]) == request.side
        )
    ]
    assets.append(asset)
    assets.sort(key=lambda item: (int(item["page_number"]), str(item["side"]), str(item["asset_id"])))
    profile["reference_assets"] = assets
    if "visual_reference" in profile:
        profile["visual_reference"] = None

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(profile),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        location = "/".join(str(part) for part in errors[0].absolute_path) or "root"
        raise ValueError(f"resulting profile violates the schema at {location}: {errors[0].message}")

    portable_relative_path(relative_asset_path)
    profile_relative = f"profiles/{output_profile_id}.profile.json"
    portable_relative_path(profile_relative)
    profiles_root.mkdir(parents=True, exist_ok=True)
    references_root.mkdir(parents=True, exist_ok=True)
    target_asset = safe_path(vault_root, relative_asset_path, allowed_prefixes=("references",), must_exist=False)
    target_profile = safe_path(vault_root, profile_relative, allowed_prefixes=("profiles",), must_exist=False)
    profile_bytes = (json.dumps(profile, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    profile_changed = not target_profile.exists() or target_profile.read_bytes() != profile_bytes
    if target_profile.exists() and profile_changed and not request.update_existing:
        raise ValueError(
            "external profile already exists with different content; pass --update-existing"
        )
    asset_created = not target_asset.exists()
    if target_asset.exists() and hashlib.sha256(target_asset.read_bytes()).hexdigest() != digest:
        raise ValueError("content-addressed destination contains unexpected bytes")
    if asset_created:
        _atomic_write(target_asset, source_bytes)
    if profile_changed:
        _atomic_write(target_profile, profile_bytes)
    return ImportResult(
        profile_id=output_profile_id,
        profile_path=target_profile,
        asset_path=target_asset,
        asset_sha256=digest,
        profile_changed=profile_changed,
        asset_created=asset_created,
    )


def _select_profile(path: Path, profile_id: str) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    values = loaded if isinstance(loaded, list) else [loaded]
    matches = [value for value in values if isinstance(value, dict) and value.get("profile_id") == profile_id]
    if len(matches) != 1:
        raise ValueError("profile manifest must contain exactly one matching profile ID")
    return json.loads(json.dumps(matches[0]))


def _regions_for_page(regions: list[dict[str, Any]], page_number: int) -> list[dict[str, Any]]:
    return [json.loads(json.dumps(region)) for region in regions if int(region["page"]) == page_number]


def _reject_runtime_input(path: Path, label: str) -> None:
    if any(part.casefold() in {"runtime", ".runtime"} for part in path.parts):
        raise ValueError(f"{label} cannot come from an application runtime directory")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault-root", type=Path, required=True)
    parser.add_argument("--profile-manifest", type=Path, required=True)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--output-profile-id")
    parser.add_argument("--asset", dest="asset_path", type=Path, required=True)
    parser.add_argument("--page-number", type=int, default=1)
    parser.add_argument("--side", choices=("front", "back", "interior", "unspecified"), default="front")
    parser.add_argument("--mime-type", choices=tuple(_MIME_SUFFIX), required=True)
    parser.add_argument("--source-url")
    parser.add_argument("--retrieval-date", default=date.today().isoformat())
    parser.add_argument(
        "--redistribution-status",
        choices=("permitted", "not_permitted", "unclear", "not_applicable"),
        required=True,
    )
    parser.add_argument("--trust-level", choices=tuple(_TRUST_RANK), required=True)
    parser.add_argument("--authorized-trusted-reference", action="store_true")
    parser.add_argument("--authorize-tier-upgrade", action="store_true")
    parser.add_argument("--update-existing", action="store_true")
    parser.add_argument("--schema", dest="schema_path", type=Path, default=DEFAULT_SCHEMA)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    result = import_reference(ImportRequest(**vars(arguments)))
    print(f"Profile: {result.profile_id}")
    print(f"Manifest: {result.profile_path}")
    print(f"Asset: {result.asset_path}")
    print(f"SHA-256: {result.asset_sha256}")
    print(f"Profile changed: {'YES' if result.profile_changed else 'NO'}")
    print(f"Asset created: {'YES' if result.asset_created else 'NO'}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI
    raise SystemExit(main())
