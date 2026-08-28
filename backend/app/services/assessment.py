"""Deterministic multidimensional investigative assessment."""

from __future__ import annotations

from backend.app.models.contracts import (
    AssessmentDimension,
    CheckStatus,
    CodeAssessment,
    DigitalSignatureAssessment,
    InvestigativeAssessment,
    LogicalConsistencyAssessment,
    MetadataAssessment,
    PdfSignatureStatus,
    ReferenceProfileAssessment,
    SimilarityAssessment,
)


def build_investigative_assessment(
    *,
    visual_risk: float,
    visual_findings: int,
    coverage: float,
    reference: ReferenceProfileAssessment | None,
    digital: DigitalSignatureAssessment | None,
    codes: CodeAssessment | None,
    metadata: MetadataAssessment | None,
    logical: LogicalConsistencyAssessment | None,
    handwriting: SimilarityAssessment | None = None,
    signature: SimilarityAssessment | None = None,
) -> InvestigativeAssessment:
    dimensions: list[AssessmentDimension] = [
        AssessmentDimension(
            dimension="visual_tampering_risk",
            status=_visual_status(visual_risk),
            score=visual_risk,
            evidence_count=visual_findings,
            explanation="Localized pixel, text, layout and compositing indicators; not an authenticity probability.",
        ),
        AssessmentDimension(
            dimension="analysis_coverage",
            status="adequate" if coverage >= 65 else "limited",
            score=coverage,
            explanation="Coverage reflects available rendering, OCR and requested checks; low coverage does not imply forgery.",
        ),
    ]
    contradictions = int(visual_risk >= 65)
    limitations = int(coverage < 65)

    if reference is not None:
        profile_score = reference.selected_profile.score if reference.selected_profile else None
        dimensions.append(
            AssessmentDimension(
                dimension="trusted_reference_strength",
                status=reference.reference_strength,
                score=profile_score,
                evidence_count=len(reference.top_matches),
                explanation=reference.explanation,
            )
        )
        limitations += int(reference.closest_fallback_used)
    if digital is not None:
        dimensions.append(
            AssessmentDimension(
                dimension="digital_signature",
                status=digital.status.value,
                evidence_count=digital.signature_count,
                explanation=digital.explanation,
            )
        )
        contradictions += int(
            digital.status in {PdfSignatureStatus.INVALID, PdfSignatureStatus.MODIFIED}
        )
        limitations += int(digital.status in {PdfSignatureStatus.UNSIGNED, PdfSignatureStatus.UNSUPPORTED})
    if codes is not None:
        dimensions.append(
            AssessmentDimension(
                dimension="qr_barcode",
                status=codes.status.value,
                evidence_count=codes.detected_count,
                explanation=codes.explanation,
            )
        )
        contradictions += int(codes.status is CheckStatus.FAILED)
        limitations += int(codes.status in {CheckStatus.UNSUPPORTED, CheckStatus.NOT_APPLICABLE})
    if metadata is not None:
        dimensions.append(
            AssessmentDimension(
                dimension="metadata_provenance",
                status=metadata.status.value,
                evidence_count=len(metadata.indicators),
                explanation=metadata.explanation,
            )
        )
        contradictions += int(metadata.status is CheckStatus.FAILED)
    if logical is not None:
        dimensions.append(
            AssessmentDimension(
                dimension="logical_consistency",
                status=logical.status.value,
                evidence_count=len(logical.results),
                explanation=logical.explanation,
            )
        )
        contradictions += int(logical.status is CheckStatus.FAILED)
        limitations += int(logical.status is CheckStatus.SKIPPED)
    for name, similarity in (("handwriting_similarity", handwriting), ("signature_similarity", signature)):
        if similarity is None:
            continue
        dimensions.append(
            AssessmentDimension(
                dimension=name,
                status=similarity.status.value,
                score=similarity.similarity_score,
                evidence_count=len(similarity.region_evidence),
                explanation=similarity.explanation,
            )
        )
        contradictions += int(similarity.status is CheckStatus.FAILED)
        limitations += int(similarity.status in {CheckStatus.SKIPPED, CheckStatus.NOT_APPLICABLE})

    if contradictions >= 2 or (
        digital is not None
        and digital.status in {PdfSignatureStatus.INVALID, PdfSignatureStatus.MODIFIED}
    ):
        status = "strong_contradictory_evidence"
        summary = "Multiple independent checks produced contradictory or suspicious evidence requiring review."
    elif contradictions:
        status = "review_recommended"
        summary = "At least one independent check produced evidence that warrants human review."
    elif limitations >= 3:
        status = "limited_evidence"
        summary = "No strong contradiction was found, but reference strength or analysis coverage is limited."
    else:
        status = "no_significant_indicators"
        summary = "No significant indicator was found in the checks that had adequate evidence."
    return InvestigativeAssessment(
        status=status,
        summary=summary,
        dimensions=dimensions,
        limitations=[
            "This is an investigative assessment, not an authenticity probability or legal identity conclusion.",
            "A clean result covers only the checks and local evidence reported above.",
        ],
    )


def _visual_status(risk: float) -> str:
    if risk >= 75:
        return "high"
    if risk >= 40:
        return "moderate"
    return "low"
