"""Versioned, deterministic profile-driven logical consistency checks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Sequence

from backend.app.docuvault.repository import DocumentProfile
from backend.app.models.contracts import (
    CheckStatus,
    LogicalConsistencyAssessment,
    LogicalRuleResult,
)


_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_NUMBER = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


@dataclass(frozen=True, slots=True)
class ExtractedField:
    field_id: str
    value: str
    confidence: float
    page_number: int
    sensitive: bool


def _normalise(value: str) -> str:
    return " ".join(token.casefold() for token in _TOKEN.findall(value))


def _word_confidence(word: Any, extraction: Any) -> float:
    value = word.confidence if getattr(word, "confidence", None) is not None else extraction.confidence
    if value is None:
        return 50.0
    return max(0.0, min(100.0, float(value) * 100.0 if float(value) <= 1.0 else float(value)))


def extract_profile_fields(
    profile: DocumentProfile,
    pages: Sequence[Any],
) -> dict[str, ExtractedField]:
    """Extract label-adjacent values without persisting or logging raw values."""

    extracted: dict[str, ExtractedField] = {}
    for definition in profile.manifest.get("fields", []):
        field_id = str(definition["field_id"])
        page_constraint = definition.get("page")
        labels = [tuple(_normalise(str(label)).split()) for label in definition["labels"]]
        best: ExtractedField | None = None
        for page_index, page in enumerate(pages, start=1):
            if page_constraint is not None and int(page_constraint) != page_index:
                continue
            words = list(page.text.words)
            normalised_words = [_normalise(str(word.text)) for word in words]
            for label_tokens in labels:
                if not label_tokens:
                    continue
                for start in range(0, len(words) - len(label_tokens) + 1):
                    if tuple(normalised_words[start : start + len(label_tokens)]) != label_tokens:
                        continue
                    label_words = words[start : start + len(label_tokens)]
                    label_y0 = min(float(word.bbox[1]) for word in label_words)
                    label_y1 = max(float(word.bbox[3]) for word in label_words)
                    label_x1 = max(float(word.bbox[2]) for word in label_words)
                    same_line = [
                        word
                        for word in words[start + len(label_tokens) :]
                        if float(word.bbox[0]) >= label_x1 - 0.015
                        and _vertical_overlap((label_y0, label_y1), (float(word.bbox[1]), float(word.bbox[3]))) >= 0.35
                        and float(word.bbox[0]) - label_x1 <= 0.62
                    ]
                    same_line.sort(key=lambda word: float(word.bbox[0]))
                    value_words: list[Any] = []
                    previous_x = label_x1
                    for word in same_line:
                        if float(word.bbox[0]) - previous_x > 0.14 and value_words:
                            break
                        token = str(word.text).strip(" :|\t")
                        if not token:
                            continue
                        value_words.append(word)
                        previous_x = float(word.bbox[2])
                        if len(value_words) >= 8:
                            break
                    value = " ".join(str(word.text).strip(" :|\t") for word in value_words).strip()
                    if not value:
                        continue
                    confidence = sum(_word_confidence(word, page.text) for word in value_words) / len(value_words)
                    candidate = ExtractedField(
                        field_id,
                        value[:240],
                        round(confidence, 1),
                        page_index,
                        bool(definition["sensitive"]),
                    )
                    if best is None or candidate.confidence > best.confidence:
                        best = candidate
        if best is not None:
            extracted[field_id] = best
    return extracted


def _vertical_overlap(first: tuple[float, float], second: tuple[float, float]) -> float:
    overlap = max(0.0, min(first[1], second[1]) - max(first[0], second[0]))
    return overlap / max(min(first[1] - first[0], second[1] - second[0]), 1e-6)


def _redact(field: ExtractedField | None) -> str | None:
    if field is None:
        return None
    value = field.value
    if not field.sensitive:
        return value[:80]
    compact = re.sub(r"\s+", " ", value).strip()
    if not compact:
        return "[redacted]"
    if any(character.isdigit() for character in compact):
        suffix = "".join(character for character in compact if character.isalnum())[-4:]
        return f"••••{suffix}" if suffix else "[redacted identifier]"
    return " ".join((part[:1] + "•" * min(max(len(part) - 1, 1), 6)) for part in compact.split())[:80]


def evaluate_logical_rules(
    profile: DocumentProfile | None,
    pages: Sequence[Any],
    *,
    fields: dict[str, ExtractedField] | None = None,
    qr_fields: dict[str, str] | None = None,
) -> LogicalConsistencyAssessment:
    if profile is None:
        return LogicalConsistencyAssessment()
    active_fields = fields if fields is not None else extract_profile_fields(profile, pages)
    text = _normalise("\n".join(str(page.text.text) for page in pages))
    page_confidences = [_page_confidence(page.text) for page in pages]
    ocr_confidence = sum(page_confidences) / len(page_confidences) if page_confidences else 0.0
    results: list[LogicalRuleResult] = []
    for rule in profile.manifest.get("logical_rules", []):
        rule_fields = [str(item) for item in rule.get("fields", [])]
        used = {field_id: active_fields.get(field_id) for field_id in rule_fields}
        exposed = {field_id: _redact(value) for field_id, value in used.items()}
        minimum = float(rule["minimum_ocr_confidence"])
        if ocr_confidence < minimum:
            results.append(
                _rule_result(
                    rule,
                    CheckStatus.SKIPPED,
                    ocr_confidence,
                    exposed,
                    f"Skipped because OCR confidence {ocr_confidence:.1f} was below {minimum:.1f}; coverage is reduced without increasing forgery risk.",
                )
            )
            continue
        missing = [field_id for field_id, value in used.items() if value is None]
        if missing and str(rule["type"]) != "fixed_text_present":
            results.append(
                _rule_result(
                    rule,
                    CheckStatus.SKIPPED,
                    ocr_confidence,
                    exposed,
                    "Skipped because the required visible fields were not extracted reliably: "
                    + ", ".join(missing)
                    + ".",
                )
            )
            continue
        try:
            passed, detail = _evaluate_rule(
                str(rule["type"]),
                rule,
                {key: value.value for key, value in used.items() if value is not None},
                text,
                qr_fields or {},
            )
        except (ValueError, TypeError, ArithmeticError) as exc:
            results.append(
                _rule_result(
                    rule,
                    CheckStatus.SKIPPED,
                    ocr_confidence,
                    exposed,
                    f"Skipped because extracted values could not be interpreted safely ({type(exc).__name__}).",
                )
            )
            continue
        status = CheckStatus.PASSED if passed else CheckStatus.FAILED
        field_confidences = [value.confidence for value in used.values() if value is not None]
        confidence = min([ocr_confidence, *field_confidences]) if field_confidences else ocr_confidence
        results.append(_rule_result(rule, status, confidence, exposed, detail))

    passed_count = sum(result.status is CheckStatus.PASSED for result in results)
    failed_count = sum(result.status is CheckStatus.FAILED for result in results)
    skipped_count = sum(result.status is CheckStatus.SKIPPED for result in results)
    if failed_count:
        status = CheckStatus.FAILED
        explanation = f"{failed_count} deterministic field-consistency rule(s) failed."
    elif passed_count:
        status = CheckStatus.PASSED
        explanation = f"{passed_count} deterministic rule(s) passed; {skipped_count} lacked reliable inputs."
    else:
        status = CheckStatus.SKIPPED
        explanation = "Profile rules were available, but OCR coverage was insufficient for a reliable decision."
    return LogicalConsistencyAssessment(
        status=status,
        passed_count=passed_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        results=results,
        explanation=explanation,
    )


def _page_confidence(extraction: Any) -> float:
    value = extraction.confidence
    if value is None:
        return 0.0
    return max(0.0, min(100.0, float(value) * 100.0 if float(value) <= 1.0 else float(value)))


def _rule_result(
    rule: dict[str, Any],
    status: CheckStatus,
    confidence: float,
    fields: dict[str, str | None],
    explanation: str,
) -> LogicalRuleResult:
    return LogicalRuleResult(
        rule_id=str(rule["rule_id"]),
        rule_version=str(rule["version"]),
        status=status,
        confidence_score=round(max(0.0, min(100.0, confidence)), 1),
        fields_used=fields,
        explanation=explanation,
    )


def _evaluate_rule(
    rule_type: str,
    rule: dict[str, Any],
    fields: dict[str, str],
    normalised_text: str,
    qr_fields: dict[str, str],
) -> tuple[bool, str]:
    ordered = [fields[field_id] for field_id in rule.get("fields", []) if field_id in fields]
    parameters = rule.get("parameters", {})
    if rule_type == "fixed_text_present":
        expected = [str(item) for item in parameters.get("texts", [])]
        missing = [item for item in expected if _normalise(item) not in normalised_text]
        return (
            not missing,
            "All configured fixed issuer text was present."
            if not missing
            else "Expected fixed text was not found: " + ", ".join(missing) + ".",
        )
    if rule_type == "regex":
        pattern = str(parameters["pattern"])
        passed = bool(re.fullmatch(pattern, ordered[0], flags=re.IGNORECASE))
        return passed, "The field matches the configured format." if passed else "The field does not match the configured format."
    if rule_type == "numeric_range":
        value = _as_number(ordered[0])
        minimum, maximum = float(parameters["minimum"]), float(parameters["maximum"])
        passed = minimum <= value <= maximum
        return passed, f"The numeric value {'is' if passed else 'is not'} within the configured {minimum:g}–{maximum:g} range."
    if rule_type == "sum_equals":
        numbers = [_as_number(value) for value in ordered]
        tolerance = float(parameters.get("tolerance", 0.01))
        passed = abs(sum(numbers[:-1]) - numbers[-1]) <= tolerance
        return passed, f"The component sum {'agrees' if passed else 'does not agree'} with the displayed total."
    if rule_type == "percentage_matches":
        total, maximum, shown = (_as_number(value) for value in ordered[:3])
        expected = total / maximum * 100.0
        tolerance = float(parameters.get("tolerance", 0.2))
        passed = abs(expected - shown) <= tolerance
        return passed, f"The displayed percentage {'agrees' if passed else 'does not agree'} with total ÷ maximum (tolerance {tolerance:g})."
    if rule_type == "date_order":
        first, second = _as_date(ordered[0]), _as_date(ordered[1])
        passed = first <= second if parameters.get("allow_equal", False) else first < second
        return passed, f"The later date {'follows' if passed else 'does not follow'} the earlier date."
    if rule_type == "age_consistency":
        born, shown_age = _as_date(ordered[0]), int(_as_number(ordered[1]))
        at_date = _as_date(ordered[2]) if len(ordered) > 2 else date.today()
        age = at_date.year - born.year - ((at_date.month, at_date.day) < (born.month, born.day))
        passed = abs(age - shown_age) <= int(parameters.get("tolerance_years", 0))
        return passed, f"Displayed age {'agrees' if passed else 'does not agree'} with the dates used."
    if rule_type in {"cross_page_equal", "qr_equals"}:
        if rule_type == "qr_equals":
            visible_id = str(rule.get("fields", [""])[0])
            right = qr_fields.get(visible_id)
            if right is None:
                raise ValueError("QR field unavailable")
            passed = _normalise(ordered[0]) == _normalise(right)
        else:
            passed = len({_normalise(value) for value in ordered}) <= 1
        return passed, f"Repeated visible values {'agree' if passed else 'do not agree'} across evidence sources."
    if rule_type == "grade_range":
        score, grade = _as_number(ordered[0]), _normalise(ordered[1])
        ranges = parameters.get("ranges", {})
        lower, upper = ranges[grade]
        passed = float(lower) <= score <= float(upper)
        return passed, f"The grade {'matches' if passed else 'does not match'} its configured score range."
    if rule_type == "status_consistency":
        failure_values = {_normalise(str(value)) for value in parameters.get("failure_values", ["fail", "f"])}
        subject_failed = any(_normalise(value) in failure_values for value in ordered[:-1])
        shown_failed = _normalise(ordered[-1]) in failure_values
        passed = subject_failed == shown_failed
        return passed, f"The displayed result status {'agrees' if passed else 'does not agree'} with subject outcomes."
    raise ValueError(f"unsupported rule type: {rule_type}")


def _as_number(value: str) -> float:
    match = _NUMBER.search(value.replace(",", ""))
    if match is None:
        raise ValueError("number unavailable")
    return float(match.group(0).replace(",", "."))


def _as_date(value: str) -> date:
    cleaned = value.strip()
    formats: Iterable[str] = (
        "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d", "%d %B %Y", "%d %b %Y"
    )
    for format_value in formats:
        try:
            return datetime.strptime(cleaned, format_value).date()
        except ValueError:
            continue
    raise ValueError("date unavailable")
