"""Embedded-text comparison and localization helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from backend.app.services.documents import TextExtraction, TextWord


@dataclass(frozen=True, slots=True)
class TextChange:
    before: str
    after: str
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class TextComparison:
    similarity: float | None
    changes: tuple[TextChange, ...]


def _token(word: TextWord) -> str:
    return re.sub(r"\W+", "", word.text, flags=re.UNICODE).casefold()


def compare_text(reference: TextExtraction, candidate: TextExtraction) -> TextComparison:
    if not reference.text and not candidate.text:
        return TextComparison(similarity=None, changes=())
    normalized_reference = " ".join(reference.text.casefold().split())
    normalized_candidate = " ".join(candidate.text.casefold().split())
    similarity = SequenceMatcher(None, normalized_reference, normalized_candidate).ratio()
    reference_tokens = [_token(word) for word in reference.words]
    candidate_tokens = [_token(word) for word in candidate.words]
    matcher = SequenceMatcher(None, reference_tokens, candidate_tokens, autojunk=False)
    changes: list[TextChange] = []
    for tag, ref_start, ref_end, cand_start, cand_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        ref_words = reference.words[ref_start:ref_end]
        cand_words = candidate.words[cand_start:cand_end]
        location_words = cand_words or ref_words
        if not location_words:
            continue
        x0 = min(word.bbox[0] for word in location_words)
        y0 = min(word.bbox[1] for word in location_words)
        x1 = max(word.bbox[2] for word in location_words)
        y1 = max(word.bbox[3] for word in location_words)
        changes.append(
            TextChange(
                before=" ".join(word.text for word in ref_words) or "(missing)",
                after=" ".join(word.text for word in cand_words) or "(removed)",
                bbox=(x0, y0, x1, y1),
            )
        )
    return TextComparison(similarity=similarity, changes=tuple(changes))
