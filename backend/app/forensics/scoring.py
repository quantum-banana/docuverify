"""Stable, explainable heuristic scoring for page and document evidence."""

from __future__ import annotations

from backend.app.models.contracts import RiskLabel, Severity


def finding_scores(
    *,
    changed_pixels: int,
    region_area: int,
    page_area: int,
    mean_delta: float,
    alignment_quality: float,
    has_text_change: bool,
    comparison_mode: str = "exact",
    region_role: str = "unknown",
    typography_inconsistency: float = 0.0,
    background_compositing: float = 0.0,
) -> tuple[float, float]:
    density = changed_pixels / max(region_area, 1)
    page_ratio = region_area / max(page_area, 1)
    intensity = min(1.0, mean_delta / 100.0)
    size_factor = min(1.0, page_ratio / 0.008)
    risk = 32.0 + 24.0 * intensity + 18.0 * min(1.0, density * 2.5) + 11.0 * size_factor
    if has_text_change:
        risk += 15.0
    if comparison_mode == "template" and region_role == "variable":
        # A changed value in a suggested variable field is informational unless
        # independent appearance evidence indicates manipulation.
        risk = min(24.0, risk * 0.28)
    elif comparison_mode == "template" and region_role == "fixed":
        risk += 12.0
    typography_inconsistency = min(1.0, max(0.0, typography_inconsistency))
    background_compositing = min(1.0, max(0.0, background_compositing))
    risk += 34.0 * typography_inconsistency + 42.0 * background_compositing
    risk = round(min(99.0, max(1.0, risk)), 1)
    confidence = 50.0 + 35.0 * alignment_quality + 8.0 * min(1.0, density * 3.0)
    if has_text_change:
        confidence += 7.0
    confidence += 4.0 * max(typography_inconsistency, background_compositing)
    return risk, round(min(99.0, max(1.0, confidence)), 1)


def overall_score(finding_risks: list[float], global_changed_ratio: float) -> float:
    if not finding_risks:
        return round(min(20.0, global_changed_ratio * 1500.0), 1)
    additional = min(6.0, max(0, len(finding_risks) - 1) * 1.5)
    return round(min(100.0, max(finding_risks) + additional), 1)


def aggregate_page_scores(page_scores: list[float]) -> float:
    """Aggregate pages without letting clean pages erase strong evidence."""

    if not page_scores:
        return 0.0
    ordered = sorted((min(100.0, max(0.0, score)) for score in page_scores), reverse=True)
    strongest = ordered[0]
    corroboration = sum(score / 100.0 for score in ordered[1:])
    return round(min(100.0, strongest + min(8.0, corroboration * 3.0)), 1)


def risk_label(score: float) -> RiskLabel:
    if score < 25:
        return RiskLabel.LOW
    if score < 50:
        return RiskLabel.MODERATE
    if score < 75:
        return RiskLabel.HIGH
    return RiskLabel.CRITICAL


def severity(score: float) -> Severity:
    if score >= 85:
        return Severity.CRITICAL
    if score >= 65:
        return Severity.HIGH
    if score >= 40:
        return Severity.MEDIUM
    if score >= 15:
        return Severity.LOW
    return Severity.INFO
