"""Deterministic multi-signal trusted-profile retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

import cv2
import numpy as np

from backend.app.docuvault.repository import DocumentProfile, ProfileRepository
from backend.app.docuvault.trust import ReferenceDecision, reference_strength
from backend.app.services.documents import render_document_page, validate_upload


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
            chosen = ProfileMatch(
                chosen.profile,
                chosen.score,
                chosen.component_scores,
                chosen.explanation + " Selected by the explicit local profile override.",
                chosen.strength,
                True,
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
        layout_score = _layout_anchor_score(manifest.get("layout_anchors", []), pages)
        page_score = _page_geometry_score(manifest["expected_pages"], pages)
        visual_score = _fixed_visual_score(profile.visual_reference_path, pages[0])
        security_score = _security_region_score(manifest["security_regions"], pages)
        expected_scripts = {str(item) for item in manifest.get("scripts", [])}
        script_score = 1.0 if not scripts or scripts & expected_scripts else 0.15
        completeness_score = float(manifest["completeness"]) / 100.0
        raw = {
            "issuer_text": issuer_score,
            "headings": heading_score,
            "layout_anchors": layout_score,
            "page_geometry": page_score,
            "fixed_visual": visual_score,
            "security_regions": security_score,
            "language_script": script_score,
            "profile_completeness": completeness_score,
        }
        total = sum(raw[name] * weight for name, weight in _COMPONENT_WEIGHTS.items()) * 100.0
        total *= 0.72 + 0.28 * float(manifest["profile_confidence"]) / 100.0
        bounded = round(max(0.0, min(100.0, total)), 1)
        components = {name: round(value * 100.0, 1) for name, value in raw.items()}
        decision = reference_strength(
            provenance=str(manifest["provenance"]["assurance"]),
            match_score=bounded,
            has_visual_reference=profile.visual_reference_path is not None,
        )
        strongest = sorted(components.items(), key=lambda item: (-item[1], item[0]))[:3]
        weakest = min(components.items(), key=lambda item: (item[1], item[0]))
        explanation = (
            "Strongest signals: "
            + ", ".join(f"{name.replace('_', ' ')} {value:.0f}" for name, value in strongest)
            + f". Weakest signal: {weakest[0].replace('_', ' ')} {weakest[1]:.0f}. "
            + decision.rationale
        )
        return ProfileMatch(profile, bounded, components, explanation, decision)


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


@lru_cache(maxsize=32)
def _reference_hash(path_value: str, modified_ns: int) -> np.ndarray:
    path = Path(path_value)
    data = path.read_bytes()
    content_type = "application/pdf" if path.suffix.casefold() == ".pdf" else "image/png"
    upload = validate_upload(
        field="profile_reference",
        filename=path.name,
        content_type=content_type,
        data=data,
        max_bytes=max(len(data), 1),
    )
    rendered = render_document_page(upload, 0, 900)
    return _perceptual_hash(rendered.image)


def _perceptual_hash(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    resized = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    low = cv2.dct(resized)[:8, :8]
    median = float(np.median(low[1:]))
    return (low > median).reshape(-1)


def _fixed_visual_score(reference_path: Path | None, page: Any) -> float:
    if reference_path is None:
        return 0.42
    try:
        reference = _reference_hash(str(reference_path), reference_path.stat().st_mtime_ns)
        image = cv2.imread(str(page.image_path), cv2.IMREAD_COLOR)
        if image is None:
            return 0.0
        candidate = _perceptual_hash(image)
        return float(1.0 - np.count_nonzero(reference != candidate) / reference.size)
    except (OSError, ValueError):
        return 0.0


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
