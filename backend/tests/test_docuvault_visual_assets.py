from __future__ import annotations

import json
from pathlib import Path

from backend.app.docuvault.visual_assets import (
    compute_visual_fingerprint,
    fixed_region_fingerprint,
    fingerprint_similarity,
    render_visual_page,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _lumen_profile() -> dict[str, object]:
    catalog = json.loads(
        (PROJECT_ROOT / "backend" / "docuvault" / "profiles" / "core.profile.json").read_text(
            encoding="utf-8"
        )
    )
    return next(
        profile
        for profile in catalog
        if profile["profile_id"] == "synthetic.lumen-grove.achievement-record.v1"
    )


def test_fixed_visual_fingerprint_ignores_variable_content_but_not_fixed_content() -> None:
    profile = _lumen_profile()
    asset = next(
        item
        for item in profile["reference_assets"]
        if item["exemplar_id"] == "reference-a"
    )
    image = render_visual_page(
        PROJECT_ROOT / asset["relative_path"],
        asset["mime_type"],
        asset["asset_page_number"],
    )
    original = fixed_region_fingerprint(
        image,
        fixed_regions=asset["fixed_region_masks"],
        variable_regions=asset["variable_region_masks"],
        page_number=1,
    )
    variable_change = image.copy()
    height, width = variable_change.shape[:2]
    for region in asset["variable_region_masks"]:
        box = region["box"]
        x0 = round(width * box["x"])
        y0 = round(height * box["y"])
        x1 = round(width * (box["x"] + box["width"]))
        y1 = round(height * (box["y"] + box["height"]))
        variable_change[y0:y1, x0:x1] = 0
    fixed_change = image.copy()
    fixed_change[round(height * 0.05) : round(height * 0.18), round(width * 0.2) : round(width * 0.8)] = 0

    variable_fingerprint = fixed_region_fingerprint(
        variable_change,
        fixed_regions=asset["fixed_region_masks"],
        variable_regions=asset["variable_region_masks"],
        page_number=1,
    )
    fixed_fingerprint = fixed_region_fingerprint(
        fixed_change,
        fixed_regions=asset["fixed_region_masks"],
        variable_regions=asset["variable_region_masks"],
        page_number=1,
    )

    assert original == asset["precomputed_fingerprint"]["value"]
    assert variable_fingerprint == original
    assert fingerprint_similarity(original, fixed_fingerprint) < 1.0

    complete = compute_visual_fingerprint(
        image,
        fixed_regions=asset["fixed_region_masks"],
        variable_regions=asset["variable_region_masks"],
        security_regions=asset["security_element_regions"],
        page_number=asset["document_page_number"],
        source_sha256=asset["sha256"],
    )
    assert complete == asset["precomputed_fingerprint"]
    assert complete["source_sha256"] == asset["sha256"]
    assert len(complete["colour_histogram"]) == 24
