from __future__ import annotations

from pathlib import Path

import fitz

from backend.app.services.documents import validate_upload
from backend.app.services.metadata import compare_document_metadata


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC = PROJECT_ROOT / "samples" / "synthetic"


def test_pdf_metadata_changes_are_bounded_exact_comparison_evidence() -> None:
    reference_data = (SYNTHETIC / "reference.pdf").read_bytes()
    with fitz.open(stream=reference_data, filetype="pdf") as document:
        changed = dict(document.metadata)
        changed.update(
            title="Different synthetic title",
            author="Different synthetic author",
            subject="Different synthetic subject",
        )
        document.set_metadata(changed)
        candidate_data = document.tobytes(no_new_id=True)

    reference = validate_upload(
        field="reference",
        filename="reference.pdf",
        content_type="application/pdf",
        data=reference_data,
        max_bytes=20 * 1024 * 1024,
    )
    candidate = validate_upload(
        field="candidate",
        filename="candidate.pdf",
        content_type="application/pdf",
        data=candidate_data,
        max_bytes=20 * 1024 * 1024,
    )

    changes = compare_document_metadata(reference, candidate)
    assert {change.field for change in changes} == {"author", "subject", "title"}
    assert all(change.reference_value and change.candidate_value for change in changes)


def test_identical_pdf_metadata_produces_no_changes() -> None:
    data = (SYNTHETIC / "reference.pdf").read_bytes()
    reference = validate_upload(
        field="reference",
        filename="reference.pdf",
        content_type="application/pdf",
        data=data,
        max_bytes=20 * 1024 * 1024,
    )
    candidate = validate_upload(
        field="candidate",
        filename="candidate.pdf",
        content_type="application/pdf",
        data=data,
        max_bytes=20 * 1024 * 1024,
    )

    assert compare_document_metadata(reference, candidate) == ()
