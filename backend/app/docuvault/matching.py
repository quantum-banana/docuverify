"""Deterministic multi-signal trusted-profile retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from typing import Any, Sequence

import cv2
import numpy as np

from backend.app.docuvault.repository import DocumentProfile, ProfileRepository
from backend.app.docuvault.trust import ReferenceDecision, reference_strength
from backend.app.docuvault.visual_assets import (
    fixed_region_fingerprint,
    fingerprint_similarity,
    render_visual_page,
)
from backend.app.forensics.alignment import align_reference


_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_COMPONENT_WEIGHTS = {
    "issuer_text": 0.22,
    "headings": 0.20,
    "layout_anchors": 0.14,
    "page_geometry": 0.10,
    "fixed_visual": 0.14,
    "security_regions": 0.08,
    "language_script": 0.04,
    "profile_completeness": 0.08,
}


@dataclass(frozen=True, slots=True)
class ProfileMatch:
    profile: DocumentProfile
    score: float
    component_scores: dict[str, float]
    explanation: str
    strength: ReferenceDecision
    selected_by_override: bool = False
    selected_exemplar_id: str | None = None
    exemplar_scores: dict[str, float] | None = None
    visual_coverage: float = 0.0
    visual_alignment_quality: float = 0.0
    visual_risk_allowed: bool = False
    visual_policy_reason: str = "No compatible visual exemplar was selected."


@dataclass(frozen=True, slots=True)
class _VisualExemplarMatch:
    score: float
    exemplar_id: str
    exemplar_scores: dict[str, float]
    coverage: float
    alignment_quality: float
    risk_allowed: bool
    policy_reason: str


@dataclass(frozen=True, slots=True)
class ProfileSearchResult:
    selected: ProfileMatch | None
    matches: tuple[ProfileMatch, ...]
    closest_fallback_used: bool
    inferred_family: str | None
    inferred_issuer: str | None


def _normalise(value: str) -> str:
    return " ".join(token.casefold() for token in _TOKEN.findall(value))


def _tokens(value: str) -> set[str]:
    return set(_normalise(value).split())


def _phrase_score(phrase: str, document_text: str, document_tokens: set[str]) -> float:
    normalised = _normalise(phrase)
    if not normalised:
        return 0.0
    if normalised in document_text:
        return 1.0
    phrase_tokens = set(normalised.split())
    coverage = len(phrase_tokens & document_tokens) / max(len(phrase_tokens), 1)
    fuzzy = SequenceMatcher(None, normalised, document_text[: max(300, len(normalised) * 8)]).ratio()
    return min(1.0, 0.82 * coverage + 0.18 * fuzzy)


class ProfileMatcher:
    def __init__(self, repository: ProfileRepository) -> None:
        self.repository = repository

    def match(
        self,
        pages: Sequence[Any],
        *,
        profile_override: str | None = None,
        limit: int = 3,
    ) -> ProfileSearchResult:
        profiles = self.repository.all_profiles()
        if not profiles or not pages:
            return ProfileSearchResult(None, (), True, None, None)
        text = _normalise("\n".join(str(page.text.text) for page in pages))
        document_tokens = set(text.split())
        scripts = _detected_scripts("\n".join(str(page.text.text) for page in pages))
        scored = [self._score(profile, pages, text, document_tokens, scripts) for profile in profiles]
        scored.sort(key=lambda item: (-item.score, item.profile.profile_id))
        override = self.repository.get(profile_override) if profile_override else None
        if profile_override and override is None:
            raise KeyError(profile_override)
        if override is not None:
            chosen = next(item for item in scored if item.profile.profile_id == override.profile_id)
            chosen = replace(
                chosen,
                explanation=(
                    chosen.explanation
                    + " Selected by the explicit local profile override."
                ),
                selected_by_override=True,
            )
            ranked = [chosen, *(item for item in scored if item.profile.profile_id != override.profile_id)]
        else:
            ranked = scored
            chosen = ranked[0]
        matches = tuple(ranked[: max(1, min(limit, 10))])
        closest = chosen.strength.tier == "Closest available profile"
        return ProfileSearchResult(
            selected=chosen,
            matches=matches,
            closest_fallback_used=closest,
            inferred_family=chosen.profile.family,
            inferred_issuer=chosen.profile.issuer,
        )

    def _score(
        self,
        profile: DocumentProfile,
        pages: Sequence[Any],
        text: str,
        document_tokens: set[str],
        scripts: set[str],
    ) -> ProfileMatch:
        manifest = profile.manifest
        issuer_phrases = [manifest["issuer"]["name"], *manifest["issuer"]["aliases"]]
        issuer_score = max(
            (_phrase_score(str(phrase), text, document_tokens) for phrase in issuer_phrases),
            default=0.0,
        )
        keyword_scores = [
            _phrase_score(str(keyword), text, document_tokens)
            for keyword in manifest.get("keywords", [])
        ]
        if keyword_scores:
            issuer_score = 0.65 * issuer_score + 0.35 * sum(keyword_scores) / len(keyword_scores)

        headings = manifest.get("stable_headings", [])
        heading_scores = [_phrase_score(str(heading), text, document_tokens) for heading in headings]
        heading_score = sum(heading_scores) / len(heading_scores) if heading_scores else 0.4
        structural_capable = profile.capability_tier in {
            "structural",
            "visual_reference",
            "cryptographic",
        }
        layout_score = (
            _layout_anchor_score(manifest.get("layout_anchors", []), pages)
            if structural_capable
            else 0.0
        )
        page_score = _page_geometry_score(manifest["expected_pages"], pages) if structural_capable else 0.0
        visual_match = _fixed_visual_score(profile, pages, text)
        visual_score = visual_match.score if visual_match is not None else None
        security_score = (
            _security_region_score(manifest["security_regions"], pages)
            if structural_capable
            else 0.0
        )
        expected_scripts = {str(item) for item in manifest.get("scripts", [])}
        script_score = 1.0 if not scripts or scripts & expected_scripts else 0.15
        completeness_score = float(manifest["completeness"]) / 100.0
        raw = {
            "issuer_text": issuer_score,
            "headings": heading_score,
            "layout_anchors": layout_score,
            "page_geometry": page_score,
            "fixed_visual": visual_score if visual_score is not None else 0.0,
            "security_regions": security_score,
            "language_script": script_score,
            "profile_completeness": completeness_score,
        }
        unavailable_components = set()
        if not structural_capable:
            unavailable_components.update(
                {"layout_anchors", "page_geometry", "security_regions", "fixed_visual"}
            )
        elif visual_score is None:
            unavailable_components.add("fixed_visual")
        active_weights = {
            name: weight
            for name, weight in _COMPONENT_WEIGHTS.items()
            if name not in unavailable_components
        }
        total = (
            sum(raw[name] * weight for name, weight in active_weights.items())
            / sum(active_weights.values())
            * 100.0
        )
        total *= 0.72 + 0.28 * float(manifest["profile_confidence"]) / 100.0
        synthetic_profile = str(manifest["provenance"]["kind"]) == "synthetic_showcase"
        if synthetic_profile and not _controlled_synthetic_candidate(profile, text):
            total *= 0.58
        bounded = round(max(0.0, min(100.0, total)), 1)
        components = {
            name: round(raw[name] * 100.0, 1)
            for name in active_weights
        }
        decision = reference_strength(
            provenance=str(manifest["provenance"]["assurance"]),
            match_score=bounded,
            has_visual_reference=profile.visual_reference_path is not None,
            capability_tier=profile.capability_tier,
            visual_reference_trust=min(
                (
                    asset.trust_level
                    for asset in (
                        profile.assets_for_exemplar(visual_match.exemplar_id)
                        if visual_match is not None
                        else profile.reference_assets
                    )
                ),
                key=lambda value: int(value[1]),
                default=None,
            ),
        )
        explained_components = {
            name: value
            for name, value in components.items()
            if name not in unavailable_components
        }
        strongest = sorted(
            explained_components.items(), key=lambda item: (-item[1], item[0])
        )[:3]
        weakest = min(explained_components.items(), key=lambda item: (item[1], item[0]))
        explanation = (
            "Strongest signals: "
            + ", ".join(f"{name.replace('_', ' ')} {value:.0f}" for name, value in strongest)
            + f". Weakest signal: {weakest[0].replace('_', ' ')} {weakest[1]:.0f}. "
            + decision.rationale
        )
        if profile.capability_tier == "metadata_only":
            explanation += (
                " This metadata-only profile used no page-geometry, region-occupancy, "
                "or pixel evidence."
            )
        elif visual_score is None:
            explanation += " No trusted visual specimen participated in this match."
        if synthetic_profile and not _controlled_synthetic_candidate(profile, text):
            explanation += (
                " Synthetic pixel evidence was disabled because the candidate did not "
                "match the controlled fictional issuer and demonstration marker; it cannot "
                "create tampering risk for this document."
            )
        if visual_match is not None:
            explanation += (
                f" Best compatible exemplar: {visual_match.exemplar_id}; "
                f"visual coverage {visual_match.coverage:.0f}% and alignment "
                f"quality {visual_match.alignment_quality:.0f}%. "
                + visual_match.policy_reason
            )
        return ProfileMatch(
            profile=profile,
            score=bounded,
            component_scores=components,
            explanation=explanation,
            strength=decision,
            selected_exemplar_id=(visual_match.exemplar_id if visual_match else None),
            exemplar_scores=(visual_match.exemplar_scores if visual_match else None),
            visual_coverage=(visual_match.coverage if visual_match else 0.0),
            visual_alignment_quality=(visual_match.alignment_quality if visual_match else 0.0),
            visual_risk_allowed=(visual_match.risk_allowed if visual_match else False),
            visual_policy_reason=(
                visual_match.policy_reason
                if visual_match
                else "No compatible visual exemplar participated in matching."
            ),
        )


def _layout_anchor_score(anchors: Sequence[dict[str, Any]], pages: Sequence[Any]) -> float:
    if not anchors:
        return 0.45
    scores: list[float] = []
    for anchor in anchors:
        page_number = int(anchor["page"])
        if page_number > len(pages):
            scores.append(0.0)
            continue
        words = pages[page_number - 1].text.words
        target_tokens = _tokens(str(anchor["text"]))
        matched = [word for word in words if _normalise(str(word.text)) in target_tokens]
        token_coverage = len({_normalise(str(word.text)) for word in matched} & target_tokens) / max(
            len(target_tokens), 1
        )
        if not matched:
            scores.append(0.0)
            continue
        x0 = min(float(word.bbox[0]) for word in matched)
        y0 = min(float(word.bbox[1]) for word in matched)
        x1 = max(float(word.bbox[2]) for word in matched)
        y1 = max(float(word.bbox[3]) for word in matched)
        expected = anchor["box"]
        expected_center = (
            float(expected["x"]) + float(expected["width"]) / 2,
            float(expected["y"]) + float(expected["height"]) / 2,
        )
        actual_center = ((x0 + x1) / 2, (y0 + y1) / 2)
        distance = float(np.hypot(actual_center[0] - expected_center[0], actual_center[1] - expected_center[1]))
        geometry = max(0.0, 1.0 - distance / max(float(anchor["tolerance"]), 0.02))
        scores.append(0.65 * token_coverage + 0.35 * geometry)
    return sum(scores) / len(scores)


def _page_geometry_score(expected: dict[str, Any], pages: Sequence[Any]) -> float:
    count = len(pages)
    count_score = 1.0 if int(expected["minimum"]) <= count <= int(expected["maximum"]) else 0.0
    orientation = str(expected["orientation"])
    orientation_scores = []
    for page in pages:
        actual = "portrait" if int(page.height) >= int(page.width) else "landscape"
        orientation_scores.append(1.0 if orientation in {"either", actual} else 0.0)
    orientation_score = sum(orientation_scores) / len(orientation_scores)
    return 0.7 * count_score + 0.3 * orientation_score


def _fixed_visual_score(
    profile: DocumentProfile, pages: Sequence[Any], document_text: str
) -> _VisualExemplarMatch | None:
    if profile.capability_tier not in {"visual_reference", "cryptographic"}:
        return None
    if not profile.reference_assets:
        return None
    synthetic_allowed = _controlled_synthetic_candidate(profile, document_text)
    if all(asset.source_class == "synthetic_demo" for asset in profile.reference_assets) and not synthetic_allowed:
        return None
    ranked: list[tuple[float, float, float, str, float]] = []
    exemplar_scores: dict[str, float] = {}
    for exemplar_id in profile.reference_exemplars():
        assets = profile.assets_for_exemplar(exemplar_id)
        fixed_scores: list[float] = []
        tie_breakers: list[float] = []
        alignments: list[float] = []
        for asset in assets:
            if asset.document_page_number > len(pages):
                continue
            try:
                candidate_image = cv2.imread(
                    str(pages[asset.document_page_number - 1].image_path),
                    cv2.IMREAD_COLOR,
                )
                if candidate_image is None:
                    continue
                reference_image = render_visual_page(
                    asset.path, asset.mime_type, asset.asset_page_number
                )
                alignment = align_reference(reference_image, candidate_image)
                inverse = np.linalg.inv(alignment.matrix)
                aligned_candidate = cv2.warpPerspective(
                    candidate_image,
                    inverse,
                    (reference_image.shape[1], reference_image.shape[0]),
                    flags=cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_CONSTANT,
                    borderValue=(255, 255, 255),
                )
                candidate_fixed = fixed_region_fingerprint(
                    aligned_candidate,
                    fixed_regions=asset.fixed_region_masks,
                    variable_regions=asset.variable_region_masks,
                    page_number=asset.document_page_number,
                )
                fixed_scores.append(
                    fingerprint_similarity(
                        str(asset.precomputed_fingerprint["value"]), candidate_fixed
                    )
                )
                if asset.variable_region_masks:
                    reference_variable = fixed_region_fingerprint(
                        reference_image,
                        fixed_regions=asset.variable_region_masks,
                        variable_regions=(),
                        page_number=asset.document_page_number,
                    )
                    candidate_variable = fixed_region_fingerprint(
                        aligned_candidate,
                        fixed_regions=asset.variable_region_masks,
                        variable_regions=(),
                        page_number=asset.document_page_number,
                    )
                    tie_breakers.append(
                        fingerprint_similarity(reference_variable, candidate_variable)
                    )
                alignments.append(float(alignment.quality))
            except (OSError, ValueError, np.linalg.LinAlgError):
                continue
        coverage = len(fixed_scores) / max(len(pages), len(assets), 1)
        fixed_score = sum(fixed_scores) / len(fixed_scores) if fixed_scores else 0.0
        tie_score = sum(tie_breakers) / len(tie_breakers) if tie_breakers else 0.0
        alignment_quality = sum(alignments) / len(alignments) if alignments else 0.0
        compatibility = fixed_score * (0.75 + 0.25 * alignment_quality) * coverage
        exemplar_scores[exemplar_id] = round(compatibility * 100.0, 1)
        ranked.append((compatibility, tie_score, alignment_quality, exemplar_id, coverage))
    if not ranked:
        return None
    compatibility, _, alignment_quality, exemplar_id, coverage = max(
        ranked, key=lambda item: (item[0], item[1], item[2], item[3] == "reference-b", item[3])
    )
    selected_assets = profile.assets_for_exemplar(exemplar_id)
    source_classes = {asset.source_class for asset in selected_assets}
    risk_enabled = all(asset.may_influence_tampering_risk for asset in selected_assets)
    if source_classes == {"synthetic_demo"}:
        risk_allowed = risk_enabled and synthetic_allowed and coverage >= 0.999 and alignment_quality >= 0.55
        policy_reason = (
            "Synthetic visual evidence may affect risk only because the candidate carries the controlled synthetic marker."
            if risk_allowed
            else "Synthetic visual evidence is retrieval-only for this candidate and cannot create tampering risk."
        )
    else:
        trusted = all(asset.trust_level in {"P2", "P3", "P4"} for asset in selected_assets)
        risk_allowed = risk_enabled and trusted and coverage >= 0.999 and alignment_quality >= 0.55
        policy_reason = (
            "Authorized visual evidence met the trust, coverage, and alignment policy."
            if risk_allowed
            else "Visual evidence has limited trust, page coverage, or alignment and cannot create strong tampering risk."
        )
    return _VisualExemplarMatch(
        score=compatibility,
        exemplar_id=exemplar_id,
        exemplar_scores=dict(sorted(exemplar_scores.items())),
        coverage=round(coverage * 100.0, 1),
        alignment_quality=round(alignment_quality * 100.0, 1),
        risk_allowed=risk_allowed,
        policy_reason=policy_reason,
    )


def _controlled_synthetic_candidate(profile: DocumentProfile, text: str) -> bool:
    if str(profile.manifest["provenance"]["kind"]) != "synthetic_showcase":
        return False
    normalised = _normalise(text)
    if "synthetic demonstration" not in normalised:
        return False
    issuers = {
        _normalise(asset.issuer)
        for asset in profile.reference_assets
        if asset.source_class == "synthetic_demo"
    }
    return bool(issuers and any(issuer and issuer in normalised for issuer in issuers))


def _security_region_score(regions: dict[str, Sequence[dict[str, Any]]], pages: Sequence[Any]) -> float:
    expected = [region for values in regions.values() for region in values]
    if not expected:
        return 0.5
    scores: list[float] = []
    images: dict[int, np.ndarray | None] = {}
    for region in expected:
        page_number = int(region["page"])
        if page_number > len(pages):
            scores.append(0.0)
            continue
        if page_number not in images:
            images[page_number] = cv2.imread(str(pages[page_number - 1].image_path), cv2.IMREAD_GRAYSCALE)
        image = images[page_number]
        if image is None:
            scores.append(0.0)
            continue
        height, width = image.shape[:2]
        box = region["box"]
        x0, y0 = int(float(box["x"]) * width), int(float(box["y"]) * height)
        x1 = min(width, int((float(box["x"]) + float(box["width"])) * width))
        y1 = min(height, int((float(box["y"]) + float(box["height"])) * height))
        crop = image[y0:y1, x0:x1]
        if crop.size == 0:
            scores.append(0.0)
            continue
        foreground = float(np.mean(crop < 235))
        edges = float(np.mean(cv2.Canny(crop, 60, 160) > 0))
        scores.append(min(1.0, foreground * 5.0 + edges * 4.0))
    return sum(scores) / len(scores)


def _detected_scripts(text: str) -> set[str]:
    scripts: set[str] = set()
    for character in text:
        code = ord(character)
        if (0x0041 <= code <= 0x024F):
            scripts.add("Latn")
        elif 0x0900 <= code <= 0x097F:
            scripts.add("Deva")
        elif 0x0980 <= code <= 0x09FF:
            scripts.add("Beng")
        elif 0x0B80 <= code <= 0x0BFF:
            scripts.add("Taml")
        elif 0x0C00 <= code <= 0x0C7F:
            scripts.add("Telu")
    return scripts
