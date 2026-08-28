"""Conservative reference-strength derivation.

The P/A/T separation is adapted from the detached DocuVault R1 export.  A
matching score never becomes an authenticity probability and cannot create
cryptographic trust.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReferenceDecision:
    tier: str
    provenance: str
    applicability: str
    rationale: str


def reference_strength(
    *,
    provenance: str,
    match_score: float,
    has_visual_reference: bool,
    direct_cryptographic_evidence: bool = False,
    exact_issued_reference: bool = False,
) -> ReferenceDecision:
    """Return a named evidence tier without implying document authenticity."""

    bounded = max(0.0, min(100.0, float(match_score)))
    if direct_cryptographic_evidence:
        return ReferenceDecision(
            "Issuer cryptographically verified",
            "P4",
            "A4",
            "A configured issuer certificate verified this exact signed revision.",
        )
    if exact_issued_reference:
        return ReferenceDecision(
            "Trusted exact issued reference",
            provenance,
            "A4",
            "The exact reference has independent trusted-source evidence.",
        )
    if provenance in {"P2", "P3"} and bounded >= 76.0:
        applicability = "A3" if has_visual_reference else "A2"
        qualifier = "including stored visual structure" if has_visual_reference else "using metadata and structural descriptors"
        return ReferenceDecision(
            "Strong trusted-profile match",
            provenance,
            applicability,
            f"Issuer, headings, layout and document characteristics matched strongly, {qualifier}.",
        )
    if provenance in {"P2", "P3"} and bounded >= 52.0:
        return ReferenceDecision(
            "Moderate trusted-profile match",
            provenance,
            "A2",
            "The official-source profile matches the likely family, with unresolved version or layout details.",
        )
    return ReferenceDecision(
        "Closest available profile",
        provenance if provenance in {"P0", "P1", "P2", "P3"} else "P0",
        "A1" if bounded > 0 else "A0",
        "This is the nearest local profile, not a sufficiently strong reference match.",
    )
