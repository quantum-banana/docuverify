"""Text comparison, field-role suggestions, and localization helpers.

The routines in this module deliberately operate on normalized word boxes.  They
therefore work with embedded PDF text and raster OCR output without knowing
which provider produced the words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum

from backend.app.services.documents import TextExtraction, TextWord


class RegionRole(StrEnum):
    """Practical comparison role for a localized document region."""

    FIXED = "fixed"
    VARIABLE = "variable"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RegionSuggestion:
    bbox: tuple[float, float, float, float]
    role: RegionRole
    confidence: float
    reason: str
    label: str | None = None


@dataclass(frozen=True, slots=True)
class TextChange:
    before: str
    after: str
    bbox: tuple[float, float, float, float]
    reference_bbox: tuple[float, float, float, float] | None = None
    candidate_bbox: tuple[float, float, float, float] | None = None
    role: RegionRole = RegionRole.UNKNOWN
    role_confidence: float = 0.5
    role_reason: str = "No stable field-label pattern was identified."
    field_label: str | None = None


@dataclass(frozen=True, slots=True)
class TextComparison:
    similarity: float | None
    changes: tuple[TextChange, ...]
    region_suggestions: tuple[RegionSuggestion, ...] = ()


_VARIABLE_LABEL_PATTERNS: tuple[tuple[str, ...], ...] = (
    ("name",),
    ("recipient",),
    ("student", "name"),
    ("candidate", "name"),
    ("identifier",),
    ("certificate", "id"),
    ("record", "id"),
    ("student", "id"),
    ("issue", "date"),
    ("date",),
    ("date", "of", "birth"),
    ("birth", "date"),
    ("dob",),
    ("address",),
    ("portrait",),
    ("photo",),
    ("qr", "code"),
    ("result",),
    ("grade",),
    ("mark",),
    ("score",),
)

_FIXED_LABEL_TOKENS = {
    token
    for pattern in _VARIABLE_LABEL_PATTERNS
    for token in pattern
}


def _token(word: TextWord) -> str:
    return re.sub(r"\W+", "", word.text, flags=re.UNICODE).casefold()


def _split_mixed_label_value_words(
    words: tuple[TextWord, ...],
) -> tuple[TextWord, ...]:
    """Split conservative ``Label: value`` OCR blocks into semantic words.

    Raster OCR providers sometimes return an entire field as one word.  Keeping
    that block intact makes a changed value look like a changed fixed label.  A
    split is made only for a recognized field label and an explicit separator;
    arbitrary OCR text is left untouched.
    """

    expanded: list[TextWord] = []
    for word in words:
        match = re.fullmatch(
            r"\s*(?P<label>[\w ]{2,36}?)\s*(?P<separator>:|#|\||[–—]|-(?=\s))\s*"
            r"(?P<value>\S.*)",
            word.text,
            flags=re.UNICODE,
        )
        if match is None:
            expanded.append(word)
            continue
        label_text = match.group("label").strip()
        label_tokens = [
            token.casefold()
            for token in re.findall(r"\w+", label_text, flags=re.UNICODE)
        ]
        matched_label = _matching_label(label_tokens)
        if matched_label is None or matched_label != " ".join(label_tokens):
            expanded.append(word)
            continue

        value_text = match.group("value").strip()
        x0, y0, x1, y1 = word.bbox
        separator_end = match.end("separator")
        split_ratio = _clamp(separator_end / max(len(word.text), 1), 0.12, 0.88)
        split_x = x0 + (x1 - x0) * split_ratio
        expanded.extend(
            (
                TextWord(label_text, (x0, y0, split_x, y1), word.confidence),
                TextWord(value_text, (split_x, y0, x1, y1), word.confidence),
            )
        )
    return tuple(expanded)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    """Small local clamp that keeps text role inference dependency-free."""

    return max(minimum, min(maximum, value))


def compare_text(
    reference: TextExtraction,
    candidate: TextExtraction,
    comparison_mode: str = "exact",
) -> TextComparison:
    if not reference.text and not candidate.text:
        return TextComparison(similarity=None, changes=())
    normalized_reference = " ".join(reference.text.casefold().split())
    normalized_candidate = " ".join(candidate.text.casefold().split())
    similarity = SequenceMatcher(None, normalized_reference, normalized_candidate).ratio()
    reference_words = _split_mixed_label_value_words(reference.words)
    candidate_words = _split_mixed_label_value_words(candidate.words)
    reference_tokens = [_token(word) for word in reference_words]
    candidate_tokens = [_token(word) for word in candidate_words]
    matcher = SequenceMatcher(None, reference_tokens, candidate_tokens, autojunk=False)
    changes: list[TextChange] = []
    for tag, ref_start, ref_end, cand_start, cand_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        ref_words = reference_words[ref_start:ref_end]
        cand_words = candidate_words[cand_start:cand_end]
        location_words = cand_words or ref_words
        if not location_words:
            continue
        reference_bbox = _words_bbox(ref_words)
        candidate_bbox = _words_bbox(cand_words)
        location_bbox = _union_boxes(reference_bbox, candidate_bbox)
        if location_bbox is None:
            continue
        role, role_confidence, role_reason, field_label = _infer_change_role(
            reference_words,
            candidate_words,
            reference_bbox,
            candidate_bbox,
            ref_words,
            cand_words,
            comparison_mode=comparison_mode,
        )
        changes.append(
            TextChange(
                before=" ".join(word.text for word in ref_words) or "(missing)",
                after=" ".join(word.text for word in cand_words) or "(removed)",
                bbox=location_bbox,
                reference_bbox=reference_bbox,
                candidate_bbox=candidate_bbox,
                role=role,
                role_confidence=role_confidence,
                role_reason=role_reason,
                field_label=field_label,
            )
        )
    suggestions = tuple(
        RegionSuggestion(
            bbox=change.bbox,
            role=change.role,
            confidence=change.role_confidence,
            reason=change.role_reason,
            label=change.field_label,
        )
        for change in changes
        if change.role is not RegionRole.UNKNOWN
    )
    return TextComparison(
        similarity=similarity,
        changes=tuple(changes),
        region_suggestions=suggestions,
    )


def suggest_variable_regions(
    reference: TextExtraction,
    candidate: TextExtraction,
) -> tuple[RegionSuggestion, ...]:
    """Return deterministic variable/fixed suggestions for changed text.

    This is intentionally a conservative layout heuristic rather than document
    understanding.  A change is suggested as variable only when a stable,
    recognizable label occupies the same line immediately to its left (or just
    above it) in both documents.
    """

    return compare_text(reference, candidate, comparison_mode="template").region_suggestions


def _words_bbox(words: tuple[TextWord, ...]) -> tuple[float, float, float, float] | None:
    if not words:
        return None
    return (
        min(word.bbox[0] for word in words),
        min(word.bbox[1] for word in words),
        max(word.bbox[2] for word in words),
        max(word.bbox[3] for word in words),
    )


def _union_boxes(
    first: tuple[float, float, float, float] | None,
    second: tuple[float, float, float, float] | None,
) -> tuple[float, float, float, float] | None:
    if first is None:
        return second
    if second is None:
        return first
    return (
        min(first[0], second[0]),
        min(first[1], second[1]),
        max(first[2], second[2]),
        max(first[3], second[3]),
    )


def _infer_change_role(
    reference_words: tuple[TextWord, ...],
    candidate_words: tuple[TextWord, ...],
    reference_bbox: tuple[float, float, float, float] | None,
    candidate_bbox: tuple[float, float, float, float] | None,
    changed_reference_words: tuple[TextWord, ...],
    changed_candidate_words: tuple[TextWord, ...],
    *,
    comparison_mode: str,
) -> tuple[RegionRole, float, str, str | None]:
    changed_tokens = {
        _token(word)
        for word in (*changed_reference_words, *changed_candidate_words)
        if _token(word)
    }
    if changed_tokens & _FIXED_LABEL_TOKENS:
        return (
            RegionRole.FIXED,
            0.93,
            "A recognized field label changed; labels are treated as fixed structure.",
            None,
        )

    reference_label = _stable_label_before(reference_words, reference_bbox)
    candidate_label = _stable_label_before(candidate_words, candidate_bbox)
    if reference_label and candidate_label and reference_label == candidate_label:
        role = RegionRole.VARIABLE if comparison_mode == "template" else RegionRole.UNKNOWN
        confidence = 0.94 if role is RegionRole.VARIABLE else 0.72
        reason = (
            f"The value follows the stable label '{reference_label}' with consistent geometry."
            if role is RegionRole.VARIABLE
            else (
                f"A stable '{reference_label}' label was found, but exact mode "
                "expects values to match."
            )
        )
        return role, confidence, reason, reference_label

    if comparison_mode == "template" and _looks_like_variable_value(
        changed_reference_words, changed_candidate_words
    ):
        return (
            RegionRole.VARIABLE,
            0.68,
            "The changed value matches a structured name, date, identifier, mark, or grade pattern.",
            None,
        )
    if _looks_like_fixed_heading(changed_reference_words, changed_candidate_words):
        return (
            RegionRole.FIXED,
            0.84,
            "Short unlabelled heading text is treated as fixed document structure.",
            None,
        )
    return (
        RegionRole.UNKNOWN,
        0.5,
        "No stable field-label pattern was identified.",
        None,
    )


def _stable_label_before(
    words: tuple[TextWord, ...],
    value_bbox: tuple[float, float, float, float] | None,
) -> str | None:
    if value_bbox is None:
        return None
    value_x0, value_y0, _, value_y1 = value_bbox
    value_height = max(0.008, value_y1 - value_y0)
    same_line = [
        word
        for word in words
        if word.bbox[2] <= value_x0 + 0.01
        and _vertical_overlap(word.bbox, value_bbox) >= 0.22
        and value_x0 - word.bbox[2] <= max(0.38, value_height * 16)
    ]
    same_line.sort(key=lambda word: word.bbox[0])
    tokens = [
        token.casefold()
        for word in same_line
        for token in re.findall(r"\w+", word.text, flags=re.UNICODE)
    ]
    matched = _matching_label(tokens)
    if matched:
        return matched

    above = [
        word
        for word in words
        if word.bbox[3] <= value_y0 + 0.005
        and value_y0 - word.bbox[3] <= max(0.055, value_height * 2.5)
        and _horizontal_overlap(word.bbox, value_bbox) >= 0.2
    ]
    above.sort(key=lambda word: (word.bbox[1], word.bbox[0]))
    return _matching_label(
        [
            token.casefold()
            for word in above
            for token in re.findall(r"\w+", word.text, flags=re.UNICODE)
        ]
    )


def _matching_label(tokens: list[str]) -> str | None:
    for pattern in sorted(_VARIABLE_LABEL_PATTERNS, key=len, reverse=True):
        size = len(pattern)
        for start in range(max(0, len(tokens) - 6), len(tokens) - size + 1):
            if tuple(tokens[start : start + size]) == pattern:
                return " ".join(pattern)
    return None


def _vertical_overlap(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    intersection = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
    return intersection / max(1e-6, min(first[3] - first[1], second[3] - second[1]))


def _horizontal_overlap(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    intersection = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
    return intersection / max(1e-6, min(first[2] - first[0], second[2] - second[0]))


def _looks_like_variable_value(
    reference_words: tuple[TextWord, ...], candidate_words: tuple[TextWord, ...]
) -> bool:
    values = [
        " ".join(word.text for word in words).strip()
        for words in (reference_words, candidate_words)
        if words
    ]
    if not values:
        return False
    return all(_matches_structured_variable_value(value) for value in values)


def _matches_structured_variable_value(value: str) -> bool:
    """Recognize values without treating every short word as a person's name."""

    patterns = (
        r"(?=.*\d)[A-Za-z0-9]+(?:[-/][A-Za-z0-9]+)+",
        r"\d{1,4}(?:[-/. ]\d{1,4}){1,2}",
        r"\d{1,2}\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{2,4}",
        r"\d{1,3}(?:\.\d+)?%?",
        r"(?:pass|fail|merit|distinction|grade\s*[A-F][+-]?)",
    )
    if any(re.fullmatch(pattern, value, flags=re.IGNORECASE) for pattern in patterns):
        return True

    name_parts = value.split()
    return (
        2 <= len(name_parts) <= 5
        and not value.isupper()
        and all(
            re.fullmatch(r"[A-Z][A-Za-z'-]{1,}", part) is not None
            for part in name_parts
        )
    )


def _looks_like_fixed_heading(
    reference_words: tuple[TextWord, ...], candidate_words: tuple[TextWord, ...]
) -> bool:
    values = [
        " ".join(word.text for word in words).strip()
        for words in (reference_words, candidate_words)
    ]
    return all(
        value
        and len(value) <= 40
        and 1 <= len(value.split()) <= 4
        and value.isupper()
        and re.fullmatch(r"[A-Z][A-Z &'/-]*", value) is not None
        for value in values
    )
