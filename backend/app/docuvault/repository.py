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


MAX_MANIFEST_BYTES = 2 * 1024 * 1024
PROFILE_SUFFIX = ".profile.json"


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


@dataclass(frozen=True, slots=True)
class ProfileDiagnostic:
    source: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class DocumentProfile:
    profile_id: str
    manifest: dict[str, Any]
    fingerprint: str
    enabled: bool
    source_name: str
    visual_reference_path: Path | None = None

    @property
    def issuer(self) -> str:
        return str(self.manifest["issuer"]["name"])

    @property
    def family(self) -> str:
        return str(self.manifest["document_family"])


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
        visual_path = self._resolve_visual_reference(value)
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
            profile_id, value, fingerprint, enabled, source_name, visual_path
        )

    def _resolve_visual_reference(self, value: dict[str, Any]) -> Path | None:
        visual = value.get("visual_reference")
        if not isinstance(visual, dict):
            return None
        relative = str(visual["relative_path"])
        path = safe_path(
            self.project_root,
            relative,
            allowed_prefixes=("samples/synthetic", "backend/docuvault/references"),
            must_exist=True,
        )
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != str(visual["sha256"]).lower():
            raise ValueError("visual reference SHA-256 does not match its manifest")
        return path

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
        }


def iter_profile_manifests(profiles: Iterable[DocumentProfile]) -> Iterable[dict[str, Any]]:
    for profile in profiles:
        yield profile.manifest
