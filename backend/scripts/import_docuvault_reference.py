"""Import and manage authorized visual references in a local DocuVault.

The workflow is deliberately local and explicit. It accepts only an authorized
source, binds every derived artifact to content hashes, and writes the profile
manifest after all referenced artifacts have been published. Questioned
job/upload files are never eligible reference inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import cv2
import fitz
import numpy as np
from jsonschema import Draft202012Validator, FormatChecker

from backend.app.docuvault.repository import MAX_MANIFEST_BYTES, MAX_REFERENCE_BYTES
from backend.app.docuvault.safe_paths import portable_relative_path, safe_path
from backend.app.docuvault.visual_assets import (
    compute_visual_fingerprint,
    region_mask_image,
    render_visual_page,
    verify_visual_media,
    visual_dimensions,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA = PROJECT_ROOT / "backend" / "docuvault" / "schemas" / "profile.v1.schema.json"
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,159}$")
_TRUST_RANK = {f"P{number}": number for number in range(5)}
_SOURCE_TRUST_CAP = {
    "synthetic_demo": 0,
    "authorized_official_specimen": 3,
    "authorized_organization_template": 2,
    "user_registered_trusted_reference": 2,
    "derived_from_multiple_trusted_exemplars": 3,
}
_MIME_SUFFIX = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}
_REQUIRED_SECURITY_KEYS = (
    "logo",
    "seal",
    "photo",
    "signature",
    "handwriting",
    "qr",
    "barcode",
)
_OPTIONAL_SECURITY_KEYS = ("mrz", "document_number", "hologram")
_REPARSE_POINT = 0x400
_THUMBNAIL_MAX_EDGE = 320


@dataclass(frozen=True, slots=True)
class ImportRequest:
    vault_root: Path
    profile_manifest: Path
    profile_id: str
    output_profile_id: str | None
    asset_path: Path
    exemplar_id: str
    document_page_number: int
    asset_page_number: int | None
    side: str
    mime_type: str
    source_class: str
    source_url: str | None
    retrieval_date: str
    redistribution_status: str
    trust_level: str
    profile_version: str
    languages: tuple[str, ...]
    creation_method: str
    licence_status_note: str
    may_influence_tampering_risk: bool
    demonstration_only: bool
    authorized_trusted_reference: bool
    authorize_tier_upgrade: bool
    confirm_profile_regions: bool
    regions_json: Path | None
    update_existing: bool = False
    schema_path: Path = DEFAULT_SCHEMA


@dataclass(frozen=True, slots=True)
class ImportResult:
    profile_id: str
    profile_path: Path
    asset_paths: tuple[Path, ...]
    asset_sha256s: tuple[str, ...]
    profile_changed: bool
    assets_created: int

    @property
    def asset_path(self) -> Path:
        """Compatibility accessor for a one-page import."""

        return self.asset_paths[0]

    @property
    def asset_sha256(self) -> str:
        """Compatibility accessor for a one-page import."""

        return self.asset_sha256s[0]

    @property
    def asset_created(self) -> bool:
        """Whether at least one page asset was newly published."""

        return self.assets_created > 0


@dataclass(frozen=True, slots=True)
class ReferenceMutationRequest:
    vault_root: Path
    profile_manifest: Path
    profile_id: str
    exemplar_id: str
    document_page_number: int
    side: str
    authorize_capability_downgrade: bool = False
    schema_path: Path = DEFAULT_SCHEMA


@dataclass(frozen=True, slots=True)
class ReferenceMutationResult:
    profile_id: str
    profile_path: Path
    asset_id: str
    action: str
    profile_changed: bool
    capability_tier: str


def import_reference(request: ImportRequest) -> ImportResult:
    """Import one raster or one/all PDF pages as authorized visual assets."""

    _validate_import_request(request)
    vault_root = _prepare_vault_root(request.vault_root)
    manifest_path = _trusted_input_file(
        request.profile_manifest, "profile manifest", vault_root=vault_root
    )
    source_path = _trusted_input_file(
        request.asset_path, "visual specimen", vault_root=vault_root
    )
    schema_path = _trusted_input_file(
        request.schema_path, "profile schema", vault_root=vault_root
    )
    if source_path.stat().st_size > MAX_REFERENCE_BYTES:
        raise ValueError("visual specimen exceeds the 64 MiB safety limit")
    if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("profile manifest exceeds the 2 MiB safety limit")
    verify_visual_media(source_path, request.mime_type)

    source_profile = _select_profile(manifest_path, request.profile_id)
    output_profile_id = request.output_profile_id or request.profile_id
    _validate_id(output_profile_id, "output profile ID")
    profiles_root = vault_root / "profiles"
    target_profile = safe_path(
        vault_root,
        f"profiles/{output_profile_id}.profile.json",
        allowed_prefixes=("profiles",),
        must_exist=False,
    )
    source_is_external = _is_within(manifest_path, profiles_root.resolve(strict=False))
    if not source_is_external and output_profile_id == request.profile_id:
        raise ValueError(
            "importing a bundled profile requires a distinct --output-profile-id; "
            "external profiles cannot silently replace bundled profiles"
        )

    if target_profile.exists():
        if target_profile.is_symlink() or _is_link_or_reparse(target_profile):
            raise ValueError("external profile manifest cannot be a link")
        if not target_profile.is_file():
            raise ValueError("external profile manifest target must be a regular file")
        if target_profile.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError("external profile manifest exceeds the 2 MiB safety limit")
        profile = _select_profile(target_profile, output_profile_id)
        if (
            profile.get("document_family") != source_profile.get("document_family")
            or profile.get("issuer", {}).get("id")
            != source_profile.get("issuer", {}).get("id")
        ):
            raise ValueError("external profile ID conflicts with a different profile definition")
    else:
        profile = _clone(source_profile)
        if not source_is_external and profile.get("reference_assets"):
            raise ValueError(
                "a bundled profile that already has assets cannot be copied without "
                "importing each referenced asset into the external vault"
            )

    profile["profile_id"] = output_profile_id
    for existing_asset in profile.get("reference_assets", []):
        existing_asset["profile_id"] = output_profile_id
    _authorize_profile_and_source(profile, request)
    region_definition = _load_region_definition(request, profile, vault_root=vault_root)
    page_mappings = _page_mappings(source_path, request)
    if page_mappings[-1][0] > int(profile["expected_pages"]["maximum"]):
        raise ValueError("imported pages exceed the profile's declared document page range")

    pending_artifacts: dict[str, bytes] = {}
    generated_assets: list[dict[str, Any]] = []
    for document_page, source_page in page_mappings:
        generated_assets.append(
            _build_asset(
                request=request,
                profile=profile,
                source_path=source_path,
                source_page=source_page,
                document_page=document_page,
                region_definition=region_definition,
                pending_artifacts=pending_artifacts,
            )
        )

    assets = [_clone(item) for item in profile.get("reference_assets", [])]
    existing_slots = {_asset_slot(item): item for item in assets}
    existing_page_slots = {
        (slot[0], slot[1]): slot for slot in existing_slots
    }
    existing_digests = {str(item["sha256"]): _asset_slot(item) for item in assets}
    generated_digests: dict[str, tuple[str, int, str]] = {}
    for generated in generated_assets:
        slot = _asset_slot(generated)
        page_slot = (slot[0], slot[1])
        occupied = existing_page_slots.get(page_slot)
        if occupied is not None and occupied != slot:
            raise ValueError(
                "an exemplar can map only one asset to each document page; use the next "
                "document page number for another side"
            )
        existing = existing_slots.get(slot)
        if existing is not None:
            if _canonical_bytes(existing) == _canonical_bytes(generated):
                continue
            raise ValueError(
                "conflicting specimen already occupies this exemplar, document page and side"
            )
        digest = str(generated["sha256"])
        duplicate_slot = existing_digests.get(digest) or generated_digests.get(digest)
        if duplicate_slot is not None and duplicate_slot[0] != slot[0]:
            raise ValueError(
                "duplicate specimen bytes are already registered in a different exemplar slot"
            )
        assets.append(generated)
        existing_slots[slot] = generated
        existing_page_slots[page_slot] = slot
        existing_digests[digest] = slot
        generated_digests[digest] = slot

    assets.sort(key=_asset_sort_key)
    profile["reference_assets"] = assets
    if "visual_reference" in profile:
        profile["visual_reference"] = None
    _validate_profile(profile, schema_path)
    profile_bytes = (json.dumps(profile, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    profile_changed = not target_profile.exists() or target_profile.read_bytes() != profile_bytes
    if target_profile.exists() and profile_changed and not request.update_existing:
        raise ValueError(
            "external profile already exists with different content; pass --update-existing"
        )

    artifact_targets: dict[Path, bytes] = {}
    for relative_path, content in pending_artifacts.items():
        portable_relative_path(relative_path)
        target = safe_path(
            vault_root,
            relative_path,
            allowed_prefixes=("references", "thumbnails", "masks", "fingerprints"),
            must_exist=False,
        )
        if target.exists() and not target.is_file():
            raise ValueError("content-addressed destination must be a regular file")
        if target.exists() and _sha256_path(target) != hashlib.sha256(content).hexdigest():
            raise ValueError("content-addressed destination contains unexpected bytes")
        artifact_targets[target] = content
    source_targets = {
        safe_path(
            vault_root,
            str(asset["relative_path"]),
            allowed_prefixes=("references",),
            must_exist=False,
        )
        for asset in generated_assets
    }
    new_source_count = sum(not path.exists() for path in source_targets)

    # The manifest is intentionally published last: an interrupted import may
    # leave an unreferenced content-addressed artifact, never a broken index.
    for target, content in sorted(artifact_targets.items(), key=lambda item: str(item[0])):
        if not target.exists():
            _atomic_write(target, content)
    if profile_changed:
        _atomic_write(target_profile, profile_bytes)

    imported_asset_paths = tuple(
        safe_path(
            vault_root,
            str(asset["relative_path"]),
            allowed_prefixes=("references",),
            must_exist=True,
        )
        for asset in generated_assets
    )
    return ImportResult(
        profile_id=output_profile_id,
        profile_path=target_profile,
        asset_paths=imported_asset_paths,
        asset_sha256s=tuple(str(asset["sha256"]) for asset in generated_assets),
        profile_changed=profile_changed,
        assets_created=new_source_count,
    )


def enable_reference(request: ReferenceMutationRequest) -> ReferenceMutationResult:
    """Enable an existing local visual asset without changing its evidence."""

    return _set_reference_enabled(request, enabled=True)


def disable_reference(request: ReferenceMutationRequest) -> ReferenceMutationResult:
    """Disable an asset when another active asset keeps the profile usable."""

    return _set_reference_enabled(request, enabled=False)


def remove_reference(request: ReferenceMutationRequest) -> ReferenceMutationResult:
    """Remove an asset record and explicitly downgrade an emptied visual profile."""

    path, container, profile, schema_path = _load_mutable_profile(request)
    asset = _find_asset(profile, request)
    remaining = [item for item in profile["reference_assets"] if item is not asset]
    active = [item for item in remaining if bool(item["enabled"])]
    if profile["capability_tier"] == "visual_reference" and not active:
        if remaining:
            raise ValueError(
                "remove disabled visual assets before removing the last active asset"
            )
        if not request.authorize_capability_downgrade:
            raise ValueError(
                "removing the last active visual asset requires explicit capability-downgrade authorization"
            )
        profile["capability_tier"] = "metadata_only"
    profile["reference_assets"] = remaining
    if "visual_reference" in profile:
        profile["visual_reference"] = None
    changed = _publish_mutation(path, container, profile, schema_path)
    return ReferenceMutationResult(
        profile_id=request.profile_id,
        profile_path=path,
        asset_id=str(asset["asset_id"]),
        action="removed",
        profile_changed=changed,
        capability_tier=str(profile["capability_tier"]),
    )


def _set_reference_enabled(
    request: ReferenceMutationRequest, *, enabled: bool
) -> ReferenceMutationResult:
    path, container, profile, schema_path = _load_mutable_profile(request)
    asset = _find_asset(profile, request)
    if bool(asset["enabled"]) == enabled:
        return ReferenceMutationResult(
            profile_id=request.profile_id,
            profile_path=path,
            asset_id=str(asset["asset_id"]),
            action="enabled" if enabled else "disabled",
            profile_changed=False,
            capability_tier=str(profile["capability_tier"]),
        )
    if not enabled:
        active_count = sum(bool(item["enabled"]) for item in profile["reference_assets"])
        if profile["capability_tier"] == "visual_reference" and active_count <= 1:
            raise ValueError(
                "the last active visual asset cannot be disabled; remove it with explicit "
                "capability-downgrade authorization instead"
            )
    asset["enabled"] = enabled
    changed = _publish_mutation(path, container, profile, schema_path)
    return ReferenceMutationResult(
        profile_id=request.profile_id,
        profile_path=path,
        asset_id=str(asset["asset_id"]),
        action="enabled" if enabled else "disabled",
        profile_changed=changed,
        capability_tier=str(profile["capability_tier"]),
    )


def _build_asset(
    *,
    request: ImportRequest,
    profile: dict[str, Any],
    source_path: Path,
    source_page: int,
    document_page: int,
    region_definition: dict[str, Any],
    pending_artifacts: dict[str, bytes],
) -> dict[str, Any]:
    rendered = render_visual_page(source_path, request.mime_type, source_page)
    if request.mime_type == "application/pdf":
        stored_bytes = _encode_png(rendered)
        stored_mime = "image/png"
        suffix = ".png"
        stored_asset_page = 1
        dimensions = {
            "width": int(rendered.shape[1]),
            "height": int(rendered.shape[0]),
            "unit": "pixels",
        }
        creation_method = (
            f"{request.creation_method.rstrip()} Source PDF page {source_page} was "
            "rendered deterministically to a bounded PNG asset."
        )
    else:
        stored_bytes = source_path.read_bytes()
        stored_mime = request.mime_type
        suffix = _MIME_SUFFIX[stored_mime]
        stored_asset_page = 1
        source_dimensions = visual_dimensions(source_path, stored_mime, 1)
        dimensions = {
            "width": source_dimensions.width,
            "height": source_dimensions.height,
            "unit": source_dimensions.unit,
        }
        creation_method = request.creation_method

    source_digest = hashlib.sha256(stored_bytes).hexdigest()
    relative_asset_path = f"references/{source_digest}{suffix}"
    _register_artifact(pending_artifacts, relative_asset_path, stored_bytes)
    fixed_masks = _regions_for_page(region_definition["fixed"], document_page)
    variable_masks = _regions_for_page(region_definition["variable"], document_page)
    if not fixed_masks:
        raise ValueError(f"document page {document_page} has no confirmed fixed-region mask")
    security_regions = {
        key: _regions_for_page(values, document_page)
        for key, values in region_definition["security"].items()
    }

    thumbnail = _thumbnail(rendered)
    thumbnail_descriptor = _thumbnail_descriptor(pending_artifacts, thumbnail)
    combined_security = tuple(
        region for values in security_regions.values() for region in values
    )
    pixel_masks = {
        "fixed": _mask_descriptor(
            pending_artifacts,
            region_mask_image(rendered.shape, fixed_masks, document_page),
        ),
        "variable": _mask_descriptor(
            pending_artifacts,
            region_mask_image(rendered.shape, variable_masks, document_page),
        ),
        "security": _mask_descriptor(
            pending_artifacts,
            region_mask_image(rendered.shape, combined_security, document_page),
        ),
    }
    fingerprint = compute_visual_fingerprint(
        rendered,
        fixed_regions=fixed_masks,
        variable_regions=variable_masks,
        security_regions=security_regions,
        page_number=document_page,
        source_sha256=source_digest,
    )
    fingerprint_bytes = (
        json.dumps(fingerprint, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    fingerprint_digest = hashlib.sha256(fingerprint_bytes).hexdigest()
    fingerprint_relative = f"fingerprints/{fingerprint_digest}.json"
    _register_artifact(pending_artifacts, fingerprint_relative, fingerprint_bytes)
    asset_id = _asset_id(
        request.exemplar_id, document_page, request.side, source_digest
    )
    return {
        "asset_id": asset_id,
        "profile_id": str(profile["profile_id"]),
        "exemplar_id": request.exemplar_id,
        "document_page_number": document_page,
        "asset_page_number": stored_asset_page,
        "side": request.side,
        "relative_path": relative_asset_path,
        "mime_type": stored_mime,
        "sha256": source_digest,
        "dimensions": dimensions,
        "source_class": request.source_class,
        "trust_level": request.trust_level,
        "issuer": str(profile["issuer"]["name"]),
        "document_family": str(profile["document_family"]),
        "profile_version": request.profile_version,
        "languages": list(_normalized_languages(request.languages)),
        "creation_method": creation_method,
        "source_url": request.source_url,
        "retrieval_date": request.retrieval_date,
        "redistribution_status": request.redistribution_status,
        "licence_status_note": request.licence_status_note,
        "may_influence_tampering_risk": request.may_influence_tampering_risk,
        "demonstration_only": request.demonstration_only,
        "enabled": True,
        "thumbnail": thumbnail_descriptor,
        "pixel_masks": pixel_masks,
        "fingerprint_file": {
            "relative_path": fingerprint_relative,
            "mime_type": "application/json",
            "sha256": fingerprint_digest,
        },
        "fixed_region_masks": fixed_masks,
        "variable_region_masks": variable_masks,
        "security_element_regions": security_regions,
        "precomputed_fingerprint": fingerprint,
    }


def _validate_import_request(request: ImportRequest) -> None:
    if not request.authorized_trusted_reference:
        raise ValueError(
            "visual specimens require explicit confirmation that the source is authorized"
        )
    _validate_id(request.profile_id, "profile ID")
    _validate_id(request.exemplar_id, "exemplar ID")
    if request.document_page_number < 1:
        raise ValueError("document page numbers start at one")
    if request.asset_page_number is not None and request.asset_page_number < 1:
        raise ValueError("asset page numbers start at one")
    if request.side not in {"front", "back", "interior", "unspecified"}:
        raise ValueError("unsupported visual-reference side")
    if request.mime_type not in _MIME_SUFFIX:
        raise ValueError("unsupported visual-reference MIME type")
    if request.source_class not in _SOURCE_TRUST_CAP:
        raise ValueError("unsupported visual-reference source class")
    if request.trust_level not in _TRUST_RANK:
        raise ValueError("unsupported visual-reference trust level")
    if _TRUST_RANK[request.trust_level] > _SOURCE_TRUST_CAP[request.source_class]:
        raise ValueError("trust level exceeds the conservative cap for this source class")
    if request.mime_type != "application/pdf" and request.asset_page_number not in {None, 1}:
        raise ValueError("raster visual references contain exactly one asset page")
    if request.confirm_profile_regions == (request.regions_json is not None):
        raise ValueError(
            "choose exactly one of --confirm-profile-regions or --regions-json"
        )
    for label, value in (
        ("profile version", request.profile_version),
        ("creation method", request.creation_method),
        ("licence/status note", request.licence_status_note),
    ):
        if not value or not value.strip():
            raise ValueError(f"{label} must be supplied explicitly")
    _normalized_languages(request.languages)
    try:
        date.fromisoformat(request.retrieval_date)
    except ValueError as exc:
        raise ValueError("retrieval date must use YYYY-MM-DD") from exc
    if (
        request.may_influence_tampering_risk
        and request.source_class != "synthetic_demo"
        and _TRUST_RANK[request.trust_level] < 2
    ):
        raise ValueError("P0/P1 imported assets cannot influence tampering risk")


def _authorize_profile_and_source(profile: dict[str, Any], request: ImportRequest) -> None:
    tier = str(profile.get("capability_tier", ""))
    if tier not in {"visual_reference", "cryptographic"}:
        if not request.authorize_tier_upgrade:
            raise ValueError(
                "attaching a specimen upgrades profile capability; pass "
                "--authorize-tier-upgrade to acknowledge this change"
            )
        profile["capability_tier"] = "visual_reference"
    profile_assurance = str(profile["provenance"]["assurance"])
    if _TRUST_RANK[request.trust_level] > _TRUST_RANK.get(profile_assurance, 0):
        raise ValueError(
            "asset trust cannot exceed profile provenance without an explicit profile review"
        )
    synthetic_profile = str(profile["provenance"]["kind"]) == "synthetic_showcase"
    synthetic_asset = request.source_class == "synthetic_demo"
    if synthetic_profile != synthetic_asset:
        raise ValueError(
            "synthetic profiles and synthetic_demo assets must remain separate from authorized references"
        )
    if synthetic_asset and (
        request.trust_level != "P0"
        or not request.demonstration_only
        or request.source_url is not None
    ):
        raise ValueError(
            "synthetic_demo assets must be P0, demonstration-only, and have no source URL"
        )


def _page_mappings(source_path: Path, request: ImportRequest) -> tuple[tuple[int, int], ...]:
    if request.mime_type != "application/pdf":
        return ((request.document_page_number, 1),)
    try:
        with fitz.open(source_path) as document:
            if document.needs_pass:
                raise ValueError("password-protected visual-reference PDFs are unsupported")
            page_count = document.page_count
    except (fitz.FileDataError, RuntimeError) as exc:
        raise ValueError("visual-reference PDF could not be inspected") from exc
    if page_count < 1:
        raise ValueError("visual-reference PDF contains no pages")
    if request.asset_page_number is not None:
        if request.asset_page_number > page_count:
            raise ValueError("asset page is outside the source PDF")
        return ((request.document_page_number, request.asset_page_number),)
    return tuple(
        (request.document_page_number + offset, source_page)
        for offset, source_page in enumerate(range(1, page_count + 1))
    )


def _load_region_definition(
    request: ImportRequest, profile: dict[str, Any], *, vault_root: Path
) -> dict[str, Any]:
    if request.confirm_profile_regions:
        return {
            "fixed": _clone(profile["regions"]["fixed"]),
            "variable": _clone(profile["regions"]["variable"]),
            "security": _normalized_security(profile["security_regions"]),
        }
    if request.regions_json is None:  # pragma: no cover - validated above
        raise ValueError("region confirmation is required")
    region_path = _trusted_input_file(
        request.regions_json, "region definition", vault_root=vault_root
    )
    if region_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("region definition exceeds the 2 MiB safety limit")
    try:
        raw = json.loads(region_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("region definition is not valid UTF-8 JSON") from exc
    if not isinstance(raw, dict):
        raise ValueError("region definition must be a JSON object")
    if "regions" in raw:
        regions = raw["regions"]
        security = raw.get("security_regions")
    else:
        regions = raw
        security = raw.get("security") or raw.get("security_element_regions")
    if not isinstance(regions, dict) or not isinstance(security, dict):
        raise ValueError("region definition requires fixed, variable and security mappings")
    fixed = regions.get("fixed") or regions.get("fixed_region_masks")
    variable = regions.get("variable")
    if variable is None:
        variable = regions.get("variable_region_masks")
    if not isinstance(fixed, list) or not isinstance(variable, list):
        raise ValueError("region definition fixed and variable values must be arrays")
    return {
        "fixed": _clone(fixed),
        "variable": _clone(variable),
        "security": _normalized_security(security),
    }


def _normalized_security(raw: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    allowed = set(_REQUIRED_SECURITY_KEYS) | set(_OPTIONAL_SECURITY_KEYS)
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unsupported security-region categories: {', '.join(sorted(unknown))}")
    normalized: dict[str, list[dict[str, Any]]] = {}
    for key in (*_REQUIRED_SECURITY_KEYS, *_OPTIONAL_SECURITY_KEYS):
        if key not in raw and key in _OPTIONAL_SECURITY_KEYS:
            continue
        value = raw.get(key, [])
        if not isinstance(value, list):
            raise ValueError(f"security region {key} must be an array")
        normalized[key] = _clone(value)
    return normalized


def _binary_descriptor(
    pending: dict[str, bytes], prefix: str, content: bytes, shape: Sequence[int]
) -> dict[str, Any]:
    digest = hashlib.sha256(content).hexdigest()
    relative = f"{prefix}/{digest}.png"
    _register_artifact(pending, relative, content)
    return {
        "relative_path": relative,
        "mime_type": "image/png",
        "sha256": digest,
        "width": int(shape[1]),
        "height": int(shape[0]),
    }


def _mask_descriptor(pending: dict[str, bytes], mask: np.ndarray) -> dict[str, Any]:
    return _binary_descriptor(pending, "masks", _encode_png(mask), mask.shape)


def _thumbnail_descriptor(
    pending: dict[str, bytes], thumbnail: np.ndarray
) -> dict[str, Any]:
    success, encoded = cv2.imencode(
        ".webp", thumbnail, [cv2.IMWRITE_WEBP_QUALITY, 82]
    )
    if not success:
        raise ValueError("visual-reference WebP thumbnail encoding failed")
    content = encoded.tobytes()
    digest = hashlib.sha256(content).hexdigest()
    relative = f"thumbnails/{digest}.webp"
    _register_artifact(pending, relative, content)
    return {
        "relative_path": relative,
        "mime_type": "image/webp",
        "sha256": digest,
        "width": int(thumbnail.shape[1]),
        "height": int(thumbnail.shape[0]),
    }


def _thumbnail(image: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    scale = min(1.0, _THUMBNAIL_MAX_EDGE / max(height, width))
    if scale == 1.0:
        return image.copy()
    return cv2.resize(
        image,
        (max(1, round(width * scale)), max(1, round(height * scale))),
        interpolation=cv2.INTER_AREA,
    )


def _encode_png(image: np.ndarray) -> bytes:
    success, encoded = cv2.imencode(
        ".png", image, [cv2.IMWRITE_PNG_COMPRESSION, 9]
    )
    if not success:
        raise ValueError("visual-reference PNG encoding failed")
    return encoded.tobytes()


def _register_artifact(pending: dict[str, bytes], relative: str, content: bytes) -> None:
    portable_relative_path(relative)
    previous = pending.get(relative)
    if previous is not None and previous != content:
        raise ValueError("content-addressed artifact collision")
    pending[relative] = content


def _asset_id(exemplar_id: str, page: int, side: str, digest: str) -> str:
    exemplar_fragment = exemplar_id[:90]
    return f"visual.{exemplar_fragment}.p{page:02d}.{side}.{digest[:12]}"


def _asset_slot(asset: Mapping[str, Any]) -> tuple[str, int, str]:
    return (
        str(asset["exemplar_id"]),
        int(asset["document_page_number"]),
        str(asset["side"]),
    )


def _asset_sort_key(asset: Mapping[str, Any]) -> tuple[str, int, str, str]:
    return (*_asset_slot(asset), str(asset["asset_id"]))


def _regions_for_page(regions: Iterable[dict[str, Any]], page: int) -> list[dict[str, Any]]:
    return [_clone(region) for region in regions if int(region["page"]) == page]


def _normalized_languages(values: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(dict.fromkeys(value.strip() for value in values if value.strip()))
    if not normalized:
        raise ValueError("at least one asset language must be supplied")
    return normalized


def _validate_id(value: str, label: str) -> None:
    if not _ID.fullmatch(value):
        raise ValueError(f"{label} is not portable")


def _select_profile(path: Path, profile_id: str) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("profile manifest is not valid UTF-8 JSON") from exc
    values = loaded if isinstance(loaded, list) else [loaded]
    matches = [
        value
        for value in values
        if isinstance(value, dict) and value.get("profile_id") == profile_id
    ]
    if len(matches) != 1:
        raise ValueError("profile manifest must contain exactly one matching profile ID")
    return _clone(matches[0])


def _load_mutable_profile(
    request: ReferenceMutationRequest,
) -> tuple[Path, dict[str, Any] | list[Any], dict[str, Any], Path]:
    _validate_id(request.profile_id, "profile ID")
    _validate_id(request.exemplar_id, "exemplar ID")
    if request.document_page_number < 1:
        raise ValueError("document page numbers start at one")
    vault_root = _prepare_vault_root(request.vault_root)
    path = _trusted_input_file(
        request.profile_manifest, "profile manifest", vault_root=vault_root
    )
    profiles_root = (vault_root / "profiles").resolve(strict=False)
    if not _is_within(path, profiles_root):
        raise ValueError("visual-reference lifecycle changes are limited to the external vault")
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("profile manifest exceeds the 2 MiB safety limit")
    try:
        container = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("profile manifest is not valid UTF-8 JSON") from exc
    values = container if isinstance(container, list) else [container]
    matches = [
        value
        for value in values
        if isinstance(value, dict) and value.get("profile_id") == request.profile_id
    ]
    if len(matches) != 1:
        raise ValueError("profile manifest must contain exactly one matching profile ID")
    profile = matches[0]
    schema_path = _trusted_input_file(
        request.schema_path, "profile schema", vault_root=vault_root
    )
    return path, container, profile, schema_path


def _find_asset(
    profile: dict[str, Any], request: ReferenceMutationRequest
) -> dict[str, Any]:
    matches = [
        asset
        for asset in profile.get("reference_assets", [])
        if _asset_slot(asset)
        == (request.exemplar_id, request.document_page_number, request.side)
    ]
    if len(matches) != 1:
        raise ValueError(
            "profile must contain exactly one matching exemplar, document page and side"
        )
    return matches[0]


def _publish_mutation(
    path: Path,
    container: dict[str, Any] | list[Any],
    profile: dict[str, Any],
    schema_path: Path,
) -> bool:
    _validate_profile(profile, schema_path)
    content = (json.dumps(container, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    changed = path.read_bytes() != content
    if changed:
        _atomic_write(path, content)
    return changed


def _validate_profile(profile: dict[str, Any], schema_path: Path) -> None:
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("profile schema is not valid UTF-8 JSON") from exc
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(profile),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        location = "/".join(str(part) for part in errors[0].absolute_path) or "root"
        raise ValueError(
            f"resulting profile violates the schema at {location}: {errors[0].message}"
        )


def _prepare_vault_root(path: Path) -> Path:
    expanded = path.expanduser()
    if expanded.exists() and _is_link_or_reparse(expanded):
        raise ValueError("external vault root cannot be a link or reparse point")
    expanded.mkdir(parents=True, exist_ok=True)
    resolved = expanded.resolve(strict=True)
    (resolved / "profiles").mkdir(parents=True, exist_ok=True)
    return resolved


def _trusted_input_file(path: Path, label: str, *, vault_root: Path) -> Path:
    expanded = path.expanduser()
    if _has_link_component(expanded):
        raise ValueError(f"{label} cannot be a link or reparse point")
    try:
        resolved = expanded.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(f"{label} does not exist") from exc
    if not resolved.is_file():
        raise ValueError(f"{label} must be a regular file")
    _reject_runtime_source(resolved, label, vault_root=vault_root)
    return resolved


def _reject_runtime_source(path: Path, label: str, *, vault_root: Path) -> None:
    parts = tuple(part.casefold() for part in path.parts)
    if any(part in {"job", "jobs", "upload", "uploads"} for part in parts):
        raise ValueError(f"{label} cannot come from an application job/upload runtime directory")
    if _is_within(path, vault_root):
        return
    if any(part in {"runtime", ".runtime"} for part in parts):
        raise ValueError(f"{label} cannot come from an application job/upload runtime directory")


def _is_link_or_reparse(path: Path) -> bool:
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(status.st_mode) or bool(
        getattr(status, "st_file_attributes", 0) & _REPARSE_POINT
    )


def _has_link_component(path: Path) -> bool:
    absolute = path.absolute()
    return any(
        _is_link_or_reparse(candidate)
        for candidate in (absolute, *absolute.parents)
        if candidate.exists()
    )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _clone(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _required_import_argument(arguments: argparse.Namespace, name: str) -> Any:
    value = getattr(arguments, name)
    if value is None or value == () or value == []:
        flag = "--" + name.replace("_", "-")
        raise ValueError(f"{flag} is required for import")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--action", choices=("import", "enable", "disable", "remove"), default="import"
    )
    parser.add_argument("--vault-root", type=Path, required=True)
    parser.add_argument("--profile-manifest", type=Path, required=True)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--output-profile-id")
    parser.add_argument("--asset", dest="asset_path", type=Path)
    parser.add_argument("--exemplar-id", required=True)
    parser.add_argument(
        "--document-page-number",
        "--page-number",
        dest="document_page_number",
        type=int,
        default=1,
    )
    parser.add_argument("--asset-page-number", type=int)
    parser.add_argument(
        "--side", choices=("front", "back", "interior", "unspecified"), default="front"
    )
    parser.add_argument("--mime-type", choices=tuple(_MIME_SUFFIX))
    parser.add_argument("--source-class", choices=tuple(_SOURCE_TRUST_CAP))
    parser.add_argument("--source-url")
    parser.add_argument("--retrieval-date", default=date.today().isoformat())
    parser.add_argument(
        "--redistribution-status",
        choices=("permitted", "not_permitted", "unclear", "not_applicable"),
    )
    parser.add_argument("--trust-level", choices=tuple(_TRUST_RANK))
    parser.add_argument("--profile-version")
    parser.add_argument("--languages", nargs="+")
    parser.add_argument("--creation-method")
    parser.add_argument("--licence-status-note")
    parser.add_argument("--may-influence-tampering-risk", action="store_true")
    parser.add_argument("--demonstration-only", action="store_true")
    parser.add_argument("--authorized-trusted-reference", action="store_true")
    parser.add_argument("--authorize-tier-upgrade", action="store_true")
    region_group = parser.add_mutually_exclusive_group()
    region_group.add_argument("--confirm-profile-regions", action="store_true")
    region_group.add_argument("--regions-json", type=Path)
    parser.add_argument("--update-existing", action="store_true")
    parser.add_argument("--authorize-capability-downgrade", action="store_true")
    parser.add_argument("--schema", dest="schema_path", type=Path, default=DEFAULT_SCHEMA)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    if arguments.action == "import":
        result = import_reference(
            ImportRequest(
                vault_root=arguments.vault_root,
                profile_manifest=arguments.profile_manifest,
                profile_id=arguments.profile_id,
                output_profile_id=arguments.output_profile_id,
                asset_path=_required_import_argument(arguments, "asset_path"),
                exemplar_id=arguments.exemplar_id,
                document_page_number=arguments.document_page_number,
                asset_page_number=arguments.asset_page_number,
                side=arguments.side,
                mime_type=_required_import_argument(arguments, "mime_type"),
                source_class=_required_import_argument(arguments, "source_class"),
                source_url=arguments.source_url,
                retrieval_date=arguments.retrieval_date,
                redistribution_status=_required_import_argument(
                    arguments, "redistribution_status"
                ),
                trust_level=_required_import_argument(arguments, "trust_level"),
                profile_version=_required_import_argument(arguments, "profile_version"),
                languages=tuple(_required_import_argument(arguments, "languages")),
                creation_method=_required_import_argument(arguments, "creation_method"),
                licence_status_note=_required_import_argument(
                    arguments, "licence_status_note"
                ),
                may_influence_tampering_risk=arguments.may_influence_tampering_risk,
                demonstration_only=arguments.demonstration_only,
                authorized_trusted_reference=arguments.authorized_trusted_reference,
                authorize_tier_upgrade=arguments.authorize_tier_upgrade,
                confirm_profile_regions=arguments.confirm_profile_regions,
                regions_json=arguments.regions_json,
                update_existing=arguments.update_existing,
                schema_path=arguments.schema_path,
            )
        )
        print(f"Profile: {result.profile_id}")
        print(f"Manifest: {result.profile_path}")
        for path, digest in zip(result.asset_paths, result.asset_sha256s, strict=True):
            print(f"Asset: {path}")
            print(f"SHA-256: {digest}")
        print(f"Profile changed: {'YES' if result.profile_changed else 'NO'}")
        print(f"Page assets created: {result.assets_created}")
        return 0

    mutation = ReferenceMutationRequest(
        vault_root=arguments.vault_root,
        profile_manifest=arguments.profile_manifest,
        profile_id=arguments.profile_id,
        exemplar_id=arguments.exemplar_id,
        document_page_number=arguments.document_page_number,
        side=arguments.side,
        authorize_capability_downgrade=arguments.authorize_capability_downgrade,
        schema_path=arguments.schema_path,
    )
    if arguments.action == "enable":
        changed = enable_reference(mutation)
    elif arguments.action == "disable":
        changed = disable_reference(mutation)
    else:
        changed = remove_reference(mutation)
    print(f"Profile: {changed.profile_id}")
    print(f"Manifest: {changed.profile_path}")
    print(f"Asset: {changed.asset_id}")
    print(f"Action: {changed.action}")
    print(f"Capability: {changed.capability_tier}")
    print(f"Profile changed: {'YES' if changed.profile_changed else 'NO'}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI
    raise SystemExit(main())
