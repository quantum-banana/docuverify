"""Validated, deterministic local profile index with SQLite state caching."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Iterable

import cv2
import numpy as np
from jsonschema import Draft202012Validator, FormatChecker

from backend.app.docuvault.safe_paths import UnsafeProfilePath, safe_path
from backend.app.docuvault.visual_assets import (
    FINGERPRINT_ALGORITHM,
    mask_fingerprint,
    region_mask_image,
    render_visual_page,
    verify_visual_media,
    visual_fingerprint_matches,
    visual_dimensions,
)


MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_REFERENCE_BYTES = 64 * 1024 * 1024
MAX_BUNDLED_REFERENCE_BYTES = 5 * 1024 * 1024
PROFILE_SUFFIX = ".profile.json"
CAPABILITY_TIERS = frozenset(
    {"metadata_only", "structural", "visual_reference", "cryptographic"}
)


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_normalized_region(region: dict[str, Any]) -> None:
    box = region["box"]
    x = float(box["x"])
    y = float(box["y"])
    width = float(box["width"])
    height = float(box["height"])
    if x + width > 1.000001 or y + height > 1.000001:
        raise ValueError("visual-reference normalized region extends beyond its page")


def _normalize_legacy_profile(value: Any) -> Any:
    """Conservatively retain pre-visual-library v1 metadata profiles.

    Legacy manifests never gain pixel capability from a path-like field. They
    remain metadata-only until an authorized importer creates complete,
    hash-bound reference-asset records.
    """

    if not isinstance(value, dict) or value.get("schema_version") != "1.0.0":
        return value
    if "capability_tier" in value and "reference_assets" in value:
        return value
    normalized = json.loads(json.dumps(value))
    normalized.setdefault("capability_tier", "metadata_only")
    normalized.setdefault("reference_assets", [])
    return normalized


@dataclass(frozen=True, slots=True)
class ProfileDiagnostic:
    source: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ReferenceAsset:
    asset_id: str
    profile_id: str
    exemplar_id: str
    document_page_number: int
    asset_page_number: int
    side: str
    path: Path
    relative_path: str
    mime_type: str
    sha256: str
    dimensions: dict[str, Any]
    source_url: str | None
    retrieval_date: str
    redistribution_status: str
    trust_level: str
    source_class: str
    issuer: str
    document_family: str
    profile_version: str
    languages: tuple[str, ...]
    creation_method: str
    licence_status_note: str
    may_influence_tampering_risk: bool
    demonstration_only: bool
    thumbnail: dict[str, Any]
    pixel_masks: dict[str, dict[str, Any]]
    fingerprint_file: dict[str, Any]
    fixed_region_masks: tuple[dict[str, Any], ...]
    variable_region_masks: tuple[dict[str, Any], ...]
    security_element_regions: dict[str, tuple[dict[str, Any], ...]]
    precomputed_fingerprint: dict[str, Any]

    @property
    def page_number(self) -> int:
        """Compatibility alias for the candidate-document page mapping."""

        return self.document_page_number


@dataclass(frozen=True, slots=True)
class DocumentProfile:
    profile_id: str
    manifest: dict[str, Any]
    fingerprint: str
    enabled: bool
    source_name: str
    visual_reference_path: Path | None = None
    reference_assets: tuple[ReferenceAsset, ...] = ()

    @property
    def issuer(self) -> str:
        return str(self.manifest["issuer"]["name"])

    @property
    def family(self) -> str:
        return str(self.manifest["document_family"])

    @property
    def capability_tier(self) -> str:
        return str(self.manifest.get("capability_tier", "metadata_only"))

    def reference_asset(
        self, page_number: int = 1, *, exemplar_id: str | None = None
    ) -> ReferenceAsset | None:
        return next(
            (
                asset
                for asset in self.reference_assets
                if asset.document_page_number == page_number
                and (exemplar_id is None or asset.exemplar_id == exemplar_id)
            ),
            None,
        )

    def reference_exemplars(self) -> tuple[str, ...]:
        return tuple(sorted({asset.exemplar_id for asset in self.reference_assets}))

    def assets_for_exemplar(self, exemplar_id: str) -> tuple[ReferenceAsset, ...]:
        return tuple(
            sorted(
                (asset for asset in self.reference_assets if asset.exemplar_id == exemplar_id),
                key=lambda asset: (asset.document_page_number, asset.side, asset.asset_id),
            )
        )


class ProfileRepository:
    """Loads only validated local manifests and stores no uploaded content."""

    def __init__(
        self,
        *,
        bundled_root: Path,
        schema_path: Path,
        index_path: Path,
        project_root: Path,
        external_root: Path | None = None,
    ) -> None:
        self.bundled_root = bundled_root.resolve()
        self.schema_path = schema_path.resolve()
        self.index_path = index_path.resolve()
        self.project_root = project_root.resolve()
        self.external_root = external_root.resolve() if external_root else None
        self._lock = RLock()
        self._profiles: tuple[DocumentProfile, ...] = ()
        self._diagnostics: tuple[ProfileDiagnostic, ...] = ()

    def startup(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS profile_state (
                    profile_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1))
                );
                CREATE TABLE IF NOT EXISTS profile_cache (
                    profile_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    manifest_json TEXT NOT NULL
                );
                """
            )
            connection.commit()
        self.reload()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.index_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _manifest_files(self) -> list[tuple[Path, Path, str]]:
        roots: list[tuple[Path, str]] = [(self.bundled_root, "bundled")]
        if self.external_root and self.external_root.is_dir():
            roots.append((self.external_root / "profiles", "external"))
        files: list[tuple[Path, Path, str]] = []
        for root, origin in roots:
            if not root.is_dir():
                continue
            for path in sorted(root.rglob(f"*{PROFILE_SUFFIX}"), key=lambda item: item.as_posix().casefold()):
                if path.is_file():
                    files.append((root, path, origin))
        return files

    def reload(self) -> tuple[DocumentProfile, ...]:
        with self._lock:
            schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
            validator = Draft202012Validator(schema, format_checker=FormatChecker())
            profiles: list[DocumentProfile] = []
            diagnostics: list[ProfileDiagnostic] = []
            seen_ids: set[str] = set()
            semantic_fingerprints: dict[str, str] = {}
            with closing(self._connect()) as connection:
                state = {
                    str(row["profile_id"]): bool(row["enabled"])
                    for row in connection.execute("SELECT profile_id, enabled FROM profile_state")
                }
                for root, path, origin in self._manifest_files():
                    source_name = f"{origin}:{path.relative_to(root).as_posix()}"
                    try:
                        if path.stat().st_size > MAX_MANIFEST_BYTES:
                            raise ValueError("manifest exceeds the 2 MiB safety limit")
                        relative = path.relative_to(root).as_posix()
                        safe_path(root, relative, must_exist=True)
                        loaded = json.loads(path.read_text(encoding="utf-8"))
                        values = loaded if isinstance(loaded, list) else [loaded]
                        if not values:
                            raise ValueError("profile catalog is empty")
                        for index, value in enumerate(values, start=1):
                            item_source = source_name if len(values) == 1 else f"{source_name}#{index}"
                            try:
                                profiles.append(
                                    self._index_profile(
                                        value=value,
                                        source_name=item_source,
                                        validator=validator,
                                        connection=connection,
                                        state=state,
                                        seen_ids=seen_ids,
                                        semantic_fingerprints=semantic_fingerprints,
                                        origin=origin,
                                    )
                                )
                            except (OSError, ValueError, UnsafeProfilePath) as exc:
                                diagnostics.append(
                                    ProfileDiagnostic(item_source, "invalid_profile", str(exc))
                                )
                    except (OSError, ValueError, json.JSONDecodeError, UnsafeProfilePath) as exc:
                        diagnostics.append(ProfileDiagnostic(source_name, "invalid_profile", str(exc)))
                connection.commit()
            profiles.sort(key=lambda profile: profile.profile_id)
            diagnostics.sort(key=lambda diagnostic: (diagnostic.source, diagnostic.code))
            self._profiles = tuple(profiles)
            self._diagnostics = tuple(diagnostics)
            return self._profiles

    def _index_profile(
        self,
        *,
        value: Any,
        source_name: str,
        validator: Draft202012Validator,
        connection: sqlite3.Connection,
        state: dict[str, bool],
        seen_ids: set[str],
        semantic_fingerprints: dict[str, str],
        origin: str,
    ) -> DocumentProfile:
        value = _normalize_legacy_profile(value)
        errors = sorted(
            validator.iter_errors(value), key=lambda error: list(error.absolute_path)
        )
        if errors:
            location = "/".join(str(part) for part in errors[0].absolute_path) or "root"
            raise ValueError(f"schema violation at {location}: {errors[0].message}")
        profile_id = str(value["profile_id"])
        if profile_id in seen_ids:
            raise ValueError(f"duplicate profile_id: {profile_id}")
        seen_ids.add(profile_id)
        fingerprint = hashlib.sha256(_canonical_bytes(value)).hexdigest()
        semantic = dict(value)
        semantic.pop("profile_id", None)
        semantic_fingerprint = hashlib.sha256(_canonical_bytes(semantic)).hexdigest()
        previous = semantic_fingerprints.get(semantic_fingerprint)
        if previous:
            raise ValueError(f"duplicate semantic profile of {previous}")
        semantic_fingerprints[semantic_fingerprint] = profile_id
        capability_tier = str(value["capability_tier"])
        if capability_tier not in CAPABILITY_TIERS:  # schema normally catches this
            raise ValueError(f"unsupported profile capability tier: {capability_tier}")
        reference_assets = self._resolve_reference_assets(value, origin=origin)
        if capability_tier in {"metadata_only", "structural"} and reference_assets:
            raise ValueError(f"{capability_tier} profiles cannot contain visual reference assets")
        if capability_tier == "visual_reference" and not reference_assets:
            raise ValueError("visual_reference profiles require at least one reference asset")
        visual_path = reference_assets[0].path if reference_assets else None
        enabled = state.get(profile_id, bool(value.get("enabled", True)))
        connection.execute(
            "INSERT OR IGNORE INTO profile_state(profile_id, enabled) VALUES (?, ?)",
            (profile_id, int(enabled)),
        )
        connection.execute(
            """
            INSERT INTO profile_cache(profile_id, fingerprint, source_name, manifest_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(profile_id) DO UPDATE SET
                fingerprint=excluded.fingerprint,
                source_name=excluded.source_name,
                manifest_json=excluded.manifest_json
            """,
            (profile_id, fingerprint, source_name, _canonical_bytes(value).decode("utf-8")),
        )
        return DocumentProfile(
            profile_id,
            value,
            fingerprint,
            enabled,
            source_name,
            visual_path,
            reference_assets,
        )

    def _resolve_reference_assets(
        self, value: dict[str, Any], *, origin: str
    ) -> tuple[ReferenceAsset, ...]:
        resolved: list[ReferenceAsset] = []
        seen_ids: set[str] = set()
        seen_slots: set[tuple[str, int]] = set()
        for raw in value.get("reference_assets", []):
            asset_id = str(raw["asset_id"])
            if asset_id in seen_ids:
                raise ValueError(f"duplicate visual-reference asset_id: {asset_id}")
            seen_ids.add(asset_id)
            if str(raw["profile_id"]) != str(value["profile_id"]):
                raise ValueError("visual-reference profile_id does not match its parent profile")
            exemplar_id = str(raw["exemplar_id"])
            document_page = int(raw["document_page_number"])
            asset_page = int(raw["asset_page_number"])
            slot = (exemplar_id, document_page)
            if slot in seen_slots:
                raise ValueError("an exemplar can map only one asset to each document page")
            seen_slots.add(slot)
            if document_page > int(value["expected_pages"]["maximum"]):
                raise ValueError("visual-reference document page exceeds the profile page range")
            relative = str(raw["relative_path"])
            path = self._resolve_artifact_path(relative, origin=origin)
            self._enforce_artifact_size(path, origin=origin)
            mime_type = str(raw["mime_type"])
            verify_visual_media(path, mime_type)
            digest = _sha256_path(path)
            if digest != str(raw["sha256"]).lower():
                raise ValueError("visual-reference SHA-256 does not match its manifest")
            profile_trust = str(value["provenance"]["assurance"])
            asset_trust = str(raw["trust_level"])
            if int(asset_trust[1]) > int(profile_trust[1]):
                raise ValueError("visual-reference trust exceeds its parent profile provenance")
            if origin == "bundled" and str(raw["redistribution_status"]) != "permitted":
                raise ValueError("bundled visual references must be redistributable")
            source_class = str(raw["source_class"])
            if source_class == "synthetic_demo":
                if asset_trust != "P0" or not bool(raw["demonstration_only"]):
                    raise ValueError("synthetic visual references must be P0 demonstration-only assets")
                if str(value["provenance"]["kind"]) != "synthetic_showcase":
                    raise ValueError("synthetic visual references require a separate synthetic profile")
            if str(raw["document_family"]) != str(value["document_family"]):
                raise ValueError("visual-reference document family does not match its profile")
            actual_dimensions = visual_dimensions(path, mime_type, asset_page)
            declared_dimensions = raw["dimensions"]
            if str(declared_dimensions["unit"]) != actual_dimensions.unit or not (
                abs(float(declared_dimensions["width"]) - actual_dimensions.width) <= 0.5
                and abs(float(declared_dimensions["height"]) - actual_dimensions.height) <= 0.5
            ):
                raise ValueError("visual-reference dimensions do not match its manifest")
            fixed_masks = tuple(raw["fixed_region_masks"])
            variable_masks = tuple(raw["variable_region_masks"])
            for region in (*fixed_masks, *variable_masks):
                _validate_normalized_region(region)
                if int(region["page"]) != document_page:
                    raise ValueError("visual-reference normalized mask uses the wrong document page")
            security = {
                key: tuple(regions)
                for key, regions in raw["security_element_regions"].items()
            }
            for regions in security.values():
                for region in regions:
                    _validate_normalized_region(region)
                if any(int(region["page"]) != document_page for region in regions):
                    raise ValueError("visual-reference security mask uses the wrong document page")
            fingerprint = raw["precomputed_fingerprint"]
            if str(fingerprint["algorithm"]) != FINGERPRINT_ALGORITHM:
                raise ValueError("unsupported visual-reference fingerprint algorithm")
            expected_mask_hash = mask_fingerprint(fixed_masks, variable_masks, security)
            if str(fingerprint["mask_sha256"]) != expected_mask_hash:
                raise ValueError("visual-reference fingerprint mask binding does not match")
            if str(fingerprint["source_sha256"]).lower() != digest:
                raise ValueError("visual-reference fingerprint is stale for its source asset")
            rendered = render_visual_page(path, mime_type, asset_page)
            if not visual_fingerprint_matches(
                fingerprint,
                rendered,
                fixed_regions=fixed_masks,
                variable_regions=variable_masks,
                security_regions=security,
                page_number=document_page,
                source_sha256=digest,
            ):
                raise ValueError("visual-reference fingerprint does not match the trusted asset")
            thumbnail = self._validate_binary_descriptor(raw["thumbnail"], origin=origin)
            if thumbnail["mime_type"] != "image/webp":
                raise ValueError("visual-reference thumbnail must be WebP")
            pixel_masks = {
                name: self._validate_binary_descriptor(descriptor, origin=origin)
                for name, descriptor in raw["pixel_masks"].items()
            }
            if any(item["mime_type"] != "image/png" for item in pixel_masks.values()):
                raise ValueError("visual-reference pixel masks must be PNG")
            expected_masks = {
                "fixed": region_mask_image(rendered.shape, fixed_masks, document_page),
                "variable": region_mask_image(rendered.shape, variable_masks, document_page),
                "security": region_mask_image(
                    rendered.shape,
                    tuple(region for regions in security.values() for region in regions),
                    document_page,
                ),
            }
            for name, expected in expected_masks.items():
                descriptor = pixel_masks[name]
                actual = cv2.imread(str(descriptor["path"]), cv2.IMREAD_GRAYSCALE)
                if actual is None or actual.shape != expected.shape:
                    raise ValueError(f"visual-reference {name} pixel mask dimensions do not match")
                if not set(int(item) for item in np.unique(actual)).issubset({0, 255}):
                    raise ValueError(f"visual-reference {name} pixel mask is not binary")
                if not np.array_equal(actual, expected):
                    raise ValueError(f"visual-reference {name} pixel mask is stale")
            fingerprint_file = self._validate_fingerprint_descriptor(
                raw["fingerprint_file"], origin=origin
            )
            if fingerprint_file["value"] != fingerprint:
                raise ValueError("visual-reference fingerprint file does not match its manifest")
            if bool(raw["enabled"]):
                resolved.append(
                    ReferenceAsset(
                        asset_id=asset_id,
                        profile_id=str(raw["profile_id"]),
                        exemplar_id=exemplar_id,
                        document_page_number=document_page,
                        asset_page_number=asset_page,
                        side=str(raw["side"]),
                        path=path,
                        relative_path=relative,
                        mime_type=mime_type,
                        sha256=str(raw["sha256"]),
                        dimensions=dict(declared_dimensions),
                        source_url=str(raw["source_url"]) if raw["source_url"] else None,
                        retrieval_date=str(raw["retrieval_date"]),
                        redistribution_status=str(raw["redistribution_status"]),
                        trust_level=asset_trust,
                        source_class=source_class,
                        issuer=str(raw["issuer"]),
                        document_family=str(raw["document_family"]),
                        profile_version=str(raw["profile_version"]),
                        languages=tuple(str(item) for item in raw["languages"]),
                        creation_method=str(raw["creation_method"]),
                        licence_status_note=str(raw["licence_status_note"]),
                        may_influence_tampering_risk=bool(raw["may_influence_tampering_risk"]),
                        demonstration_only=bool(raw["demonstration_only"]),
                        thumbnail=thumbnail,
                        pixel_masks=pixel_masks,
                        fingerprint_file=fingerprint_file,
                        fixed_region_masks=fixed_masks,
                        variable_region_masks=variable_masks,
                        security_element_regions=security,
                        precomputed_fingerprint=json.loads(json.dumps(fingerprint)),
                    )
                )
        resolved.sort(
            key=lambda item: (
                item.exemplar_id,
                item.document_page_number,
                item.side,
                item.asset_id,
            )
        )
        return tuple(resolved)

    def _resolve_artifact_path(self, relative: str, *, origin: str) -> Path:
        if origin == "bundled":
            return safe_path(
                self.project_root,
                relative,
                allowed_prefixes=(
                    "samples/synthetic",
                    "backend/docuvault/references",
                    "backend/docuvault/assets/synthetic",
                ),
                must_exist=True,
            )
        if self.external_root is None:  # pragma: no cover - origin guarantees it
            raise ValueError("external visual-reference root is unavailable")
        return safe_path(
            self.external_root,
            relative,
            allowed_prefixes=("references", "thumbnails", "masks", "fingerprints"),
            must_exist=True,
        )

    @staticmethod
    def _enforce_artifact_size(path: Path, *, origin: str) -> None:
        limit = MAX_BUNDLED_REFERENCE_BYTES if origin == "bundled" else MAX_REFERENCE_BYTES
        if path.stat().st_size > limit:
            label = "5 MiB tracked" if origin == "bundled" else "64 MiB external"
            raise ValueError(f"visual-reference artifact exceeds the {label} safety limit")

    def _validate_binary_descriptor(
        self, raw: dict[str, Any], *, origin: str
    ) -> dict[str, Any]:
        path = self._resolve_artifact_path(str(raw["relative_path"]), origin=origin)
        self._enforce_artifact_size(path, origin=origin)
        if _sha256_path(path) != str(raw["sha256"]).lower():
            raise ValueError("visual-reference related artifact SHA-256 does not match")
        mime_type = str(raw["mime_type"])
        suffixes = {
            "image/png": {".png"},
            "image/jpeg": {".jpg", ".jpeg"},
            "image/webp": {".webp"},
        }.get(mime_type)
        if suffixes is None or path.suffix.casefold() not in suffixes:
            raise ValueError("visual-reference related artifact suffix/MIME mismatch")
        header = path.read_bytes()[:16]
        magic_matches = (
            mime_type == "image/png" and header.startswith(b"\x89PNG\r\n\x1a\n")
        ) or (
            mime_type == "image/jpeg" and header.startswith(b"\xff\xd8\xff")
        ) or (
            mime_type == "image/webp"
            and header.startswith(b"RIFF")
            and header[8:12] == b"WEBP"
        )
        if not magic_matches:
            raise ValueError("visual-reference related artifact magic/MIME mismatch")
        image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if image is None:
            raise ValueError("visual-reference related image could not be decoded")
        height, width = image.shape[:2]
        if width != int(raw["width"]) or height != int(raw["height"]):
            raise ValueError("visual-reference related image dimensions do not match")
        return {
            "path": path,
            "relative_path": str(raw["relative_path"]),
            "mime_type": mime_type,
            "sha256": str(raw["sha256"]).lower(),
            "width": width,
            "height": height,
        }

    def _validate_fingerprint_descriptor(
        self, raw: dict[str, Any], *, origin: str
    ) -> dict[str, Any]:
        path = self._resolve_artifact_path(str(raw["relative_path"]), origin=origin)
        self._enforce_artifact_size(path, origin=origin)
        if str(raw["mime_type"]) != "application/json" or path.suffix.casefold() != ".json":
            raise ValueError("visual-reference fingerprint file suffix/MIME mismatch")
        if _sha256_path(path) != str(raw["sha256"]).lower():
            raise ValueError("visual-reference fingerprint-file SHA-256 does not match")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("visual-reference fingerprint file is invalid") from exc
        return {
            "path": path,
            "relative_path": str(raw["relative_path"]),
            "mime_type": str(raw["mime_type"]),
            "sha256": str(raw["sha256"]).lower(),
            "value": value,
        }

    @property
    def diagnostics(self) -> tuple[ProfileDiagnostic, ...]:
        return self._diagnostics

    def all_profiles(self, *, include_disabled: bool = False) -> tuple[DocumentProfile, ...]:
        if include_disabled:
            return self._profiles
        return tuple(profile for profile in self._profiles if profile.enabled)

    def get(self, profile_id: str, *, include_disabled: bool = False) -> DocumentProfile | None:
        return next(
            (
                profile
                for profile in self._profiles
                if profile.profile_id == profile_id and (include_disabled or profile.enabled)
            ),
            None,
        )

    def search(
        self,
        *,
        issuer: str | None = None,
        document_family: str | None = None,
        year: int | None = None,
        language: str | None = None,
    ) -> tuple[DocumentProfile, ...]:
        issuer_query = (issuer or "").casefold().strip()
        family_query = (document_family or "").casefold().strip()
        language_query = (language or "").casefold().strip()
        matches: list[DocumentProfile] = []
        for profile in self.all_profiles():
            manifest = profile.manifest
            if issuer_query and issuer_query not in (
                str(manifest["issuer"]["id"]) + " " + str(manifest["issuer"]["name"])
            ).casefold():
                continue
            if family_query and family_query not in str(manifest["document_family"]).casefold():
                continue
            if year is not None:
                years = manifest.get("years") or []
                validity = manifest.get("validity") or {}
                if years and year not in years:
                    continue
                if validity.get("from_year") and year < int(validity["from_year"]):
                    continue
                if validity.get("to_year") and year > int(validity["to_year"]):
                    continue
            if language_query and language_query not in {
                str(item).casefold() for item in manifest.get("languages", [])
            }:
                continue
            matches.append(profile)
        return tuple(matches)

    def set_enabled(self, profile_id: str, enabled: bool) -> DocumentProfile:
        with self._lock:
            existing = self.get(profile_id, include_disabled=True)
            if existing is None:
                raise KeyError(profile_id)
            with closing(self._connect()) as connection:
                connection.execute(
                    "INSERT INTO profile_state(profile_id, enabled) VALUES (?, ?) "
                    "ON CONFLICT(profile_id) DO UPDATE SET enabled=excluded.enabled",
                    (profile_id, int(enabled)),
                )
                connection.commit()
            self._profiles = tuple(
                DocumentProfile(
                    profile.profile_id,
                    profile.manifest,
                    profile.fingerprint,
                    enabled if profile.profile_id == profile_id else profile.enabled,
                    profile.source_name,
                    profile.visual_reference_path,
                    profile.reference_assets,
                )
                for profile in self._profiles
            )
            updated = self.get(profile_id, include_disabled=True)
            if updated is None:  # pragma: no cover - guarded by the lookup above
                raise KeyError(profile_id)
            return updated

    def fingerprints(self) -> dict[str, str]:
        return {profile.profile_id: profile.fingerprint for profile in self._profiles}

    def stats(self) -> dict[str, int]:
        return {
            "profiles": len(self._profiles),
            "enabled": sum(1 for profile in self._profiles if profile.enabled),
            "invalid": len(self._diagnostics),
            "families": len({profile.family for profile in self._profiles}),
            "with_visual_reference": sum(
                1 for profile in self._profiles if profile.visual_reference_path is not None
            ),
            "metadata_only": sum(
                profile.capability_tier == "metadata_only" for profile in self._profiles
            ),
            "structural": sum(
                profile.capability_tier == "structural" for profile in self._profiles
            ),
            "visual_reference": sum(
                profile.capability_tier == "visual_reference" for profile in self._profiles
            ),
            "cryptographic": sum(
                profile.capability_tier == "cryptographic" for profile in self._profiles
            ),
        }


def iter_profile_manifests(profiles: Iterable[DocumentProfile]) -> Iterable[dict[str, Any]]:
    for profile in profiles:
        yield profile.manifest
