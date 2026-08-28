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

from jsonschema import Draft202012Validator, FormatChecker

from backend.app.docuvault.safe_paths import UnsafeProfilePath, safe_path
from backend.app.docuvault.visual_assets import (
    FINGERPRINT_ALGORITHM,
    fixed_region_fingerprint,
    mask_fingerprint,
    render_visual_page,
    verify_visual_media,
    visual_dimensions,
)


MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_REFERENCE_BYTES = 64 * 1024 * 1024
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


@dataclass(frozen=True, slots=True)
class ProfileDiagnostic:
    source: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ReferenceAsset:
    asset_id: str
    profile_id: str
    page_number: int
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
    fixed_region_masks: tuple[dict[str, Any], ...]
    variable_region_masks: tuple[dict[str, Any], ...]
    security_element_regions: dict[str, tuple[dict[str, Any], ...]]
    precomputed_fingerprint: dict[str, str]


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

    def reference_asset(self, page_number: int = 1) -> ReferenceAsset | None:
        return next(
            (asset for asset in self.reference_assets if asset.page_number == page_number),
            None,
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
        seen_pages: set[tuple[int, str]] = set()
        for raw in value.get("reference_assets", []):
            asset_id = str(raw["asset_id"])
            if asset_id in seen_ids:
                raise ValueError(f"duplicate visual-reference asset_id: {asset_id}")
            seen_ids.add(asset_id)
            if str(raw["profile_id"]) != str(value["profile_id"]):
                raise ValueError("visual-reference profile_id does not match its parent profile")
            page_and_side = (int(raw["page_number"]), str(raw["side"]))
            if page_and_side in seen_pages:
                raise ValueError("duplicate visual-reference page and side")
            seen_pages.add(page_and_side)
            relative = str(raw["relative_path"])
            if origin == "bundled":
                path = safe_path(
                    self.project_root,
                    relative,
                    allowed_prefixes=("samples/synthetic", "backend/docuvault/references"),
                    must_exist=True,
                )
            else:
                if self.external_root is None:  # pragma: no cover - origin guarantees it
                    raise ValueError("external visual-reference root is unavailable")
                path = safe_path(
                    self.external_root,
                    relative,
                    allowed_prefixes=("references",),
                    must_exist=True,
                )
            if path.stat().st_size > MAX_REFERENCE_BYTES:
                raise ValueError("visual reference exceeds the 64 MiB safety limit")
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
            actual_dimensions = visual_dimensions(path, mime_type, int(raw["page_number"]))
            declared_dimensions = raw["dimensions"]
            if str(declared_dimensions["unit"]) != actual_dimensions.unit or not (
                abs(float(declared_dimensions["width"]) - actual_dimensions.width) <= 0.5
                and abs(float(declared_dimensions["height"]) - actual_dimensions.height) <= 0.5
            ):
                raise ValueError("visual-reference dimensions do not match its manifest")
            fixed_masks = tuple(raw["fixed_region_masks"])
            variable_masks = tuple(raw["variable_region_masks"])
            fingerprint = raw["precomputed_fingerprint"]
            if str(fingerprint["algorithm"]) != FINGERPRINT_ALGORITHM:
                raise ValueError("unsupported visual-reference fingerprint algorithm")
            expected_mask_hash = mask_fingerprint(fixed_masks, variable_masks)
            if str(fingerprint["mask_sha256"]) != expected_mask_hash:
                raise ValueError("visual-reference fingerprint mask binding does not match")
            rendered = render_visual_page(path, mime_type, int(raw["page_number"]))
            actual_fingerprint = fixed_region_fingerprint(
                rendered,
                fixed_regions=fixed_masks,
                variable_regions=variable_masks,
                page_number=int(raw["page_number"]),
            )
            if str(fingerprint["value"]).lower() != actual_fingerprint:
                raise ValueError("visual-reference fingerprint does not match the trusted asset")
            security = {
                key: tuple(regions)
                for key, regions in raw["security_element_regions"].items()
            }
            resolved.append(
                ReferenceAsset(
                    asset_id=asset_id,
                    profile_id=str(raw["profile_id"]),
                    page_number=int(raw["page_number"]),
                    side=str(raw["side"]),
                    path=path,
                    relative_path=relative,
                    mime_type=mime_type,
                    sha256=str(raw["sha256"]),
                    dimensions=dict(declared_dimensions),
                    source_url=str(raw["source_url"]) if raw["source_url"] else None,
                    retrieval_date=str(raw["retrieval_date"]),
                    redistribution_status=str(raw["redistribution_status"]),
                    trust_level=str(raw["trust_level"]),
                    fixed_region_masks=fixed_masks,
                    variable_region_masks=variable_masks,
                    security_element_regions=security,
                    precomputed_fingerprint={
                        "algorithm": str(fingerprint["algorithm"]),
                        "value": str(fingerprint["value"]).lower(),
                        "mask_sha256": str(fingerprint["mask_sha256"]),
                    },
                )
            )
        resolved.sort(key=lambda item: (item.page_number, item.side, item.asset_id))
        return tuple(resolved)

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
