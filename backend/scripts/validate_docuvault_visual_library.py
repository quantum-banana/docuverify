"""Validate the complete bundled DocuVault visual and evaluation libraries."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from jsonschema import Draft202012Validator, FormatChecker

from backend.app.docuvault.repository import (
    MAX_BUNDLED_REFERENCE_BYTES,
    ProfileRepository,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROFILE_ROOT = PROJECT_ROOT / "backend" / "docuvault" / "profiles"
SCHEMA_PATH = PROJECT_ROOT / "backend" / "docuvault" / "schemas" / "profile.v1.schema.json"
ASSET_ROOT = PROJECT_ROOT / "backend" / "docuvault" / "assets" / "synthetic"
EVALUATION_ROOT = PROJECT_ROOT / "samples" / "docuvault-visual-evaluation"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_profiles() -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    for path in sorted(PROFILE_ROOT.glob("*.profile.json")):
        loaded = json.loads(path.read_text(encoding="utf-8"))
        profiles.extend(loaded if isinstance(loaded, list) else [loaded])
    return profiles


def validate_library() -> dict[str, Any]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    profiles = _load_profiles()
    schema_errors: list[str] = []
    for profile in profiles:
        for error in sorted(
            validator.iter_errors(profile), key=lambda item: list(item.absolute_path)
        ):
            location = "/".join(str(part) for part in error.absolute_path) or "root"
            schema_errors.append(f"{profile.get('profile_id', 'unknown')}:{location}:{error.message}")
    if schema_errors:
        raise ValueError("profile schema validation failed: " + " | ".join(schema_errors[:10]))

    with tempfile.TemporaryDirectory(prefix="docuverify-visual-library-") as temporary:
        repository = ProfileRepository(
            bundled_root=PROFILE_ROOT,
            schema_path=SCHEMA_PATH,
            index_path=Path(temporary) / "profile-index.sqlite3",
            project_root=PROJECT_ROOT,
        )
        repository.startup()
        if repository.diagnostics:
            raise ValueError(
                "repository validation failed: "
                + " | ".join(diagnostic.message for diagnostic in repository.diagnostics)
            )
        stats = repository.stats()

    profile_by_id = {str(profile["profile_id"]): profile for profile in profiles}
    manifests = sorted(
        path for path in ASSET_ROOT.glob("*/manifest.json") if path.parent.name != "_shared"
    )
    manifest_assets = 0
    logical_pages: set[tuple[str, int]] = set()
    logical_masks: set[tuple[str, int, str]] = set()
    fingerprint_files: set[str] = set()
    thumbnails: set[str] = set()
    reference_counts: Counter[str] = Counter()
    legitimate_pairs_checked = 0
    for path in manifests:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        required = {
            "schema_version",
            "profile_id",
            "source_profile_id",
            "profile_version",
            "issuer",
            "document_family",
            "display_family",
            "source_class",
            "trust_level",
            "creation_method",
            "source_url",
            "retrieval_date",
            "redistribution_status",
            "licence_status_note",
            "may_influence_tampering_risk",
            "demonstration_only",
            "pages",
        }
        missing = required - set(manifest)
        if missing:
            raise ValueError(f"visual manifest {path.name} misses: {sorted(missing)}")
        profile = profile_by_id.get(str(manifest["profile_id"]))
        if profile is None or profile["capability_tier"] != "visual_reference":
            raise ValueError("visual manifest has no enabled visual profile")
        profile_assets = {
            (
                str(asset["exemplar_id"]),
                int(asset["document_page_number"]),
                str(asset["asset_id"]),
            ): asset
            for asset in profile["reference_assets"]
            if bool(asset.get("enabled", True))
        }
        manifest_keys: set[tuple[str, int, str]] = set()
        exemplar_assets: dict[tuple[str, int], dict[str, Any]] = {}
        for item in manifest["pages"]:
            asset = item["asset"]
            key = (
                str(asset["exemplar_id"]),
                int(asset["document_page_number"]),
                str(asset["asset_id"]),
            )
            if profile_assets.get(key) != asset:
                raise ValueError("visual manifest and profile asset records diverge")
            manifest_keys.add(key)
            exemplar = str(asset["exemplar_id"])
            document_page = int(asset["document_page_number"])
            exemplar_assets[(exemplar, document_page)] = asset
            reference_counts[exemplar] += 1
            if asset["source_class"] != "synthetic_demo" or not asset["demonstration_only"]:
                raise ValueError("bundled visual library contains a non-synthetic asset")
            manifest_assets += 1
            profile_id = str(manifest["profile_id"])
            page = int(asset["document_page_number"])
            logical_pages.add((profile_id, page))
            thumbnails.add(str(asset["thumbnail"]["relative_path"]))
            fingerprint_files.add(str(asset["fingerprint_file"]["relative_path"]))
            for kind in ("fixed", "variable", "security"):
                logical_masks.add((profile_id, page, kind))
        if set(profile_assets) != manifest_keys:
            raise ValueError("profile contains a visual asset absent from its asset manifest")
        for document_page in sorted({page for _, page in exemplar_assets}):
            left = exemplar_assets.get(("reference-a", document_page))
            right = exemplar_assets.get(("reference-b", document_page))
            if left is None or right is None:
                raise ValueError("every synthetic visual page requires reference-a and reference-b")
            if left["variable_region_masks"] != right["variable_region_masks"]:
                raise ValueError("legitimate exemplars use different variable-region definitions")
            left_image = cv2.imread(
                str(PROJECT_ROOT / str(left["relative_path"])), cv2.IMREAD_COLOR
            )
            right_image = cv2.imread(
                str(PROJECT_ROOT / str(right["relative_path"])), cv2.IMREAD_COLOR
            )
            variable_mask = cv2.imread(
                str(PROJECT_ROOT / str(left["pixel_masks"]["variable"]["relative_path"])),
                cv2.IMREAD_GRAYSCALE,
            )
            if (
                left_image is None
                or right_image is None
                or variable_mask is None
                or left_image.shape != right_image.shape
                or variable_mask.shape != left_image.shape[:2]
            ):
                raise ValueError("legitimate exemplar variation validation could not decode an asset")
            changed = np.max(cv2.absdiff(left_image, right_image), axis=2) > 4
            changed_pixels = int(np.count_nonzero(changed))
            outside_variable = int(np.count_nonzero(changed & (variable_mask == 0)))
            if changed_pixels < 50:
                raise ValueError("reference-a and reference-b do not contain meaningful variable values")
            if outside_variable / max(changed_pixels, 1) > 0.005:
                raise ValueError(
                    "legitimate A/B exemplar differences escape the declared variable mask"
                )
            legitimate_pairs_checked += 1

    evaluation_manifests = sorted(EVALUATION_ROOT.glob("*/ground-truth/manifest.json"))
    clean_references = 0
    questioned = 0
    for path in evaluation_manifests:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("synthetic") is not True:
            raise ValueError("evaluation manifest is not explicitly synthetic")
        for name in ("reference_a", "reference_b"):
            reference = path.parents[1] / str(manifest["truth"][name])
            if not reference.is_file():
                raise ValueError(f"missing clean evaluation reference: {reference}")
            expected = str(manifest["truth"][f"{name}_sha256"])
            if _sha256(reference) != expected:
                raise ValueError("clean evaluation reference hash is stale")
            clean_references += 1
        for item in manifest["questioned"]:
            questioned_path = EVALUATION_ROOT / str(item["file"])
            if not questioned_path.is_file() or item.get("production_access_permitted") is not False:
                raise ValueError("evaluation questioned document is missing or production-enabled")
            if _sha256(questioned_path) != str(item.get("sha256", "")):
                raise ValueError("evaluation questioned document hash is missing or stale")
            questioned += 1

    generated_files = sorted(
        [path for path in ASSET_ROOT.rglob("*") if path.is_file()]
        + [path for path in EVALUATION_ROOT.rglob("*") if path.is_file()]
    )
    hashes = Counter(_sha256(path) for path in generated_files)
    duplicates = [digest for digest, count in hashes.items() if count > 1]
    if duplicates:
        raise ValueError(f"duplicate generated binary/content detected: {len(duplicates)} groups")
    oversized = [path for path in generated_files if path.stat().st_size > MAX_BUNDLED_REFERENCE_BYTES]
    if oversized:
        raise ValueError(f"tracked visual artifact exceeds 5 MiB: {oversized[0]}")
    forbidden_suffixes = {".pem", ".key", ".pfx", ".p12", ".jks"}
    secrets = [path for path in generated_files if path.suffix.casefold() in forbidden_suffixes]
    if secrets:
        raise ValueError(f"private-key-like artifact found: {secrets[0]}")

    production_files = [
        path
        for root in (PROJECT_ROOT / "backend" / "app", PROFILE_ROOT)
        for path in root.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".py", ".json"}
    ]
    forbidden_reference = "docuvault-visual-evaluation"
    if any(
        forbidden_reference in path.read_text(encoding="utf-8", errors="ignore")
        for path in production_files
    ):
        raise ValueError("production code or profiles reference evaluation ground truth")

    expected_counts = {
        "profiles": 39,
        "families": 19,
        "visual_profiles": 20,
        "metadata_only_profiles": 19,
        "asset_manifests": 20,
        "reference_a": 27,
        "reference_b": 27,
        "reference_assets": 54,
        "logical_pages": 27,
        "thumbnails": 54,
        "fingerprints": 54,
        "evaluation_folders": 20,
        "clean_references": 40,
        "questioned_documents": 100,
        "ground_truth_manifests": 20,
    }
    actual_counts = {
        "profiles": stats["profiles"],
        "families": stats["families"],
        "visual_profiles": stats["visual_reference"],
        "metadata_only_profiles": stats["metadata_only"],
        "asset_manifests": len(manifests),
        "reference_a": reference_counts["reference-a"],
        "reference_b": reference_counts["reference-b"],
        "reference_assets": manifest_assets,
        "logical_pages": len(logical_pages),
        "thumbnails": len(thumbnails),
        "fingerprints": len(fingerprint_files),
        "evaluation_folders": len(evaluation_manifests),
        "clean_references": clean_references,
        "questioned_documents": questioned,
        "ground_truth_manifests": len(evaluation_manifests),
    }
    if actual_counts != expected_counts:
        raise ValueError(
            f"visual-library count contract changed: expected {expected_counts}, got {actual_counts}"
        )

    return {
        "profiles": stats["profiles"],
        "families": stats["families"],
        "visual_profiles": stats["visual_reference"],
        "metadata_only_profiles": stats["metadata_only"],
        "asset_manifests": len(manifests),
        "reference_assets": manifest_assets,
        "reference_a": reference_counts["reference-a"],
        "reference_b": reference_counts["reference-b"],
        "logical_pages": len(logical_pages),
        "legitimate_pairs_checked": legitimate_pairs_checked,
        "thumbnails": len(thumbnails),
        "fixed_masks": len({item for item in logical_masks if item[2] == "fixed"}),
        "variable_masks": len({item for item in logical_masks if item[2] == "variable"}),
        "security_masks": len({item for item in logical_masks if item[2] == "security"}),
        "fingerprints": len(fingerprint_files),
        "evaluation_folders": len(evaluation_manifests),
        "clean_references": clean_references,
        "questioned_documents": questioned,
        "ground_truth_manifests": len(evaluation_manifests),
        "generated_files": len(generated_files),
        "total_bytes": sum(path.stat().st_size for path in generated_files),
        "largest_file": max(
            generated_files, key=lambda path: path.stat().st_size
        ).relative_to(PROJECT_ROOT).as_posix(),
        "largest_file_bytes": max(path.stat().st_size for path in generated_files),
        "duplicate_content_groups": 0,
        "production_access_to_ground_truth": False,
    }


def _parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description=__doc__)


def main() -> int:
    _parser().parse_args()
    print(json.dumps(validate_library(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
