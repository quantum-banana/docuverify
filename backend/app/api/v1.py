"""Phase 1 REST, SSE, and safe evidence-asset endpoints."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Annotated, AsyncIterator

from fastapi import APIRouter, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from backend.app import __version__
from backend.app.core.storage import utc_now
from backend.app.models.contracts import (
    AnalysisJob,
    BoundingBox,
    CapabilityStatus,
    ComparisonMode,
    CreateAnalysisResponse,
    DiagnosticsResponse,
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
    ProfileCatalogResponse,
    ProfileMatchSummary,
    ProfileStateRequest,
)
from backend.app.docuvault.repository import DocumentProfile
from backend.app.services.documents import DocumentValidationError, ValidatedUpload, validate_upload
from backend.app.services.ocr import raster_ocr_capability
from backend.app.services.biometric_similarity import RegionSelection
from backend.app.services.pipeline import (
    AnalysisOptions,
    _humanize_profile_value,
    _profile_capability,
    _profile_display_name,
    _profile_reference_asset_summary,
    _reference_capability_label,
)


router = APIRouter(prefix="/api/v1")
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class APIProblem(Exception):
    def __init__(self, status_code: int, error: ErrorDetail) -> None:
        super().__init__(error.message)
        self.status_code = status_code
        self.error = error


@router.get("/health", response_model=HealthResponse, tags=["system"])
def health(request: Request) -> HealthResponse:
    runtime_writable = request.app.state.store.runtime_writable()
    raster_ocr, _, _ = _raster_ocr_capability(request)
    profile_stats = request.app.state.profiles.stats()
    return HealthResponse(
        status="ok" if runtime_writable else "degraded",
        version=__version__,
        current_time=utc_now(),
        capabilities=CapabilityStatus(
            uploads=True,
            pdf_rendering=True,
            image_rendering=True,
            alignment=True,
            visual_comparison=True,
            embedded_pdf_text=True,
            raster_ocr=raster_ocr,
            sse=True,
            multi_page=True,
            template_comparison=True,
            docuvault_profiles=profile_stats["enabled"] > 0,
            qr_decoding=True,
            pdf_signature_validation=True,
            metadata_forensics=True,
            logical_rules=True,
            handwriting_comparison=True,
            signature_comparison=True,
        ),
    )


@router.get("/diagnostics", response_model=DiagnosticsResponse, tags=["system"])
def diagnostics(request: Request) -> DiagnosticsResponse:
    import platform
    import shutil
    import subprocess

    import cv2
    import fitz
    import numpy as np

    gpu_detected = False
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            probe = subprocess.run(
                [nvidia_smi, "--query-gpu=name", "--format=csv,noheader"],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=creation_flags,
            )
            gpu_detected = probe.returncode == 0 and bool(probe.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            gpu_detected = False
    runtime_writable = request.app.state.store.runtime_writable()
    _, ocr_provider, ocr_device = _raster_ocr_capability(request)
    profile_stats = request.app.state.profiles.stats()
    try:
        import pyhanko

        signature_provider = f"pyHanko {getattr(pyhanko, '__version__', '0.36.2')}"
    except ImportError:
        signature_provider = "unavailable"
    return DiagnosticsResponse(
        python_version=platform.python_version(),
        ocr_provider=ocr_provider,
        ocr_device=ocr_device,
        opencv_version=cv2.__version__,
        pymupdf_version=str(getattr(fitz, "VersionBind", fitz.version[0])),
        numpy_version=np.__version__,
        gpu_detected=gpu_detected,
        backend_ready=runtime_writable,
        runtime_writable=runtime_writable,
        docuvault_profile_count=profile_stats["enabled"],
        docuvault_invalid_profile_count=profile_stats["invalid"],
        pdf_signature_provider=signature_provider,
        pdf_trust_store_mode="explicit_local_store",
    )


@router.get("/profiles", response_model=ProfileCatalogResponse, tags=["profiles"])
def list_profiles(
    request: Request,
    issuer: Annotated[str | None, Query(max_length=160)] = None,
    document_family: Annotated[str | None, Query(max_length=160)] = None,
    year: Annotated[int | None, Query(ge=1900, le=2200)] = None,
    language: Annotated[str | None, Query(max_length=32)] = None,
) -> ProfileCatalogResponse:
    repository = request.app.state.profiles
    profiles = repository.search(
        issuer=issuer,
        document_family=document_family,
        year=year,
        language=language,
    )
    stats = repository.stats()
    return ProfileCatalogResponse(
        profiles=[_catalog_profile_summary(profile) for profile in profiles],
        profile_count=stats["profiles"],
        enabled_count=stats["enabled"],
        invalid_count=stats["invalid"],
    )


@router.patch(
    "/profiles/{profile_id}/state",
    response_model=ProfileMatchSummary,
    responses={404: {"model": ErrorResponse}},
    tags=["profiles"],
)
def set_profile_state(
    profile_id: str,
    state: ProfileStateRequest,
    request: Request,
) -> ProfileMatchSummary:
    try:
        profile = request.app.state.profiles.set_enabled(profile_id, state.enabled)
    except KeyError as exc:
        raise APIProblem(
            404,
            ErrorDetail(code="profile_not_found", message="The requested local profile was not found."),
        ) from exc
    return _catalog_profile_summary(profile)


@router.post(
    "/analyses/reference",
    response_model=CreateAnalysisResponse,
    status_code=202,
    responses={400: {"model": ErrorResponse}, 413: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    tags=["analysis"],
)
async def create_reference_analysis(
    request: Request,
    reference: Annotated[UploadFile, File(description="Trusted 1-10 page reference")],
    candidate: Annotated[UploadFile, File(description="Questioned 1-10 page document")],
    comparison_mode: Annotated[str, Form()] = "exact",
    handwriting_exemplars: Annotated[list[UploadFile] | None, File()] = None,
    signature_exemplars: Annotated[list[UploadFile] | None, File()] = None,
    handwriting_regions: Annotated[str | None, Form(max_length=12000)] = None,
    signature_regions: Annotated[str | None, Form(max_length=12000)] = None,
) -> CreateAnalysisResponse:
    try:
        mode = ComparisonMode(comparison_mode)
    except ValueError:
        raise APIProblem(
            422,
            ErrorDetail(
                code="unsupported_comparison_mode",
                message="comparison_mode must be exact or template.",
                field="comparison_mode",
                details={"supported": ["exact", "template"]},
            ),
        )
    settings = request.app.state.settings
    try:
        reference_data, candidate_data = await asyncio.gather(
            reference.read(settings.max_upload_bytes + 1),
            candidate.read(settings.max_upload_bytes + 1),
        )
        validated_reference = validate_upload(
            field="reference",
            filename=reference.filename,
            content_type=reference.content_type,
            data=reference_data,
            max_bytes=settings.max_upload_bytes,
        )
        validated_candidate = validate_upload(
            field="candidate",
            filename=candidate.filename,
            content_type=candidate.content_type,
            data=candidate_data,
            max_bytes=settings.max_upload_bytes,
        )
        for upload in (validated_reference, validated_candidate):
            if upload.page_count > settings.max_pages:
                raise DocumentValidationError(
                    "page_limit_exceeded",
                    (
                        f"The {upload.field} PDF exceeds the configured "
                        f"{settings.max_pages}-page limit."
                    ),
                    field=upload.field,
                    details={
                        "page_count": upload.page_count,
                        "max_pages": settings.max_pages,
                    },
                )
        validated_handwriting = await _validate_exemplars(
            handwriting_exemplars or [],
            field="handwriting_exemplars",
            request=request,
            minimum=1,
            maximum=5,
        )
        validated_signatures = await _validate_exemplars(
            signature_exemplars or [],
            field="signature_exemplars",
            request=request,
            minimum=2,
            maximum=5,
        )
        selected_handwriting_regions = _parse_region_selections(
            handwriting_regions, field="handwriting_regions"
        )
        selected_signature_regions = _parse_region_selections(
            signature_regions, field="signature_regions"
        )
    except DocumentValidationError as exc:
        status = 413 if exc.code == "file_too_large" else 422
        raise APIProblem(
            status,
            ErrorDetail(
                code=exc.code,
                message=exc.message,
                field=exc.field,
                details=exc.details,
            ),
        ) from exc
    finally:
        await reference.close()
        await candidate.close()
        for upload in [*(handwriting_exemplars or []), *(signature_exemplars or [])]:
            await upload.close()
    return request.app.state.manager.submit(
        validated_reference,
        validated_candidate,
        comparison_mode=mode,
        options=AnalysisOptions(
            handwriting_exemplars=validated_handwriting,
            signature_exemplars=validated_signatures,
            handwriting_regions=selected_handwriting_regions,
            signature_regions=selected_signature_regions,
        ),
    )


@router.post(
    "/analyses/automatic",
    response_model=CreateAnalysisResponse,
    status_code=202,
    responses={400: {"model": ErrorResponse}, 413: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    tags=["analysis"],
)
async def create_automatic_analysis(
    request: Request,
    candidate: Annotated[UploadFile, File(description="Questioned 1-10 page document")],
    profile_override: Annotated[str | None, Form(max_length=160)] = None,
    handwriting_exemplars: Annotated[list[UploadFile] | None, File()] = None,
    signature_exemplars: Annotated[list[UploadFile] | None, File()] = None,
    handwriting_regions: Annotated[str | None, Form(max_length=12000)] = None,
    signature_regions: Annotated[str | None, Form(max_length=12000)] = None,
) -> CreateAnalysisResponse:
    settings = request.app.state.settings
    try:
        candidate_data = await candidate.read(settings.max_upload_bytes + 1)
        validated_candidate = validate_upload(
            field="candidate",
            filename=candidate.filename,
            content_type=candidate.content_type,
            data=candidate_data,
            max_bytes=settings.max_upload_bytes,
        )
        if validated_candidate.page_count > settings.max_pages:
            raise DocumentValidationError(
                "page_limit_exceeded",
                f"The candidate PDF exceeds the configured {settings.max_pages}-page limit.",
                field="candidate",
                details={
                    "page_count": validated_candidate.page_count,
                    "max_pages": settings.max_pages,
                },
            )
        if profile_override and request.app.state.profiles.get(profile_override) is None:
            raise DocumentValidationError(
                "profile_not_found",
                "The selected local profile is unavailable or disabled.",
                field="profile_override",
            )
        validated_handwriting = await _validate_exemplars(
            handwriting_exemplars or [],
            field="handwriting_exemplars",
            request=request,
            minimum=1,
            maximum=5,
        )
        validated_signatures = await _validate_exemplars(
            signature_exemplars or [],
            field="signature_exemplars",
            request=request,
            minimum=2,
            maximum=5,
        )
        selected_handwriting_regions = _parse_region_selections(
            handwriting_regions, field="handwriting_regions"
        )
        selected_signature_regions = _parse_region_selections(
            signature_regions, field="signature_regions"
        )
    except DocumentValidationError as exc:
        status = 413 if exc.code == "file_too_large" else 422
        raise APIProblem(
            status,
            ErrorDetail(code=exc.code, message=exc.message, field=exc.field, details=exc.details),
        ) from exc
    finally:
        await candidate.close()
        for upload in [*(handwriting_exemplars or []), *(signature_exemplars or [])]:
            await upload.close()
    return request.app.state.manager.submit_automatic(
        validated_candidate,
        options=AnalysisOptions(
            profile_override=profile_override,
            handwriting_exemplars=validated_handwriting,
            signature_exemplars=validated_signatures,
            handwriting_regions=selected_handwriting_regions,
            signature_regions=selected_signature_regions,
        ),
    )


@router.post(
    "/demo/reference",
    response_model=CreateAnalysisResponse,
    status_code=202,
    tags=["analysis"],
)
def create_demo_analysis(request: Request) -> CreateAnalysisResponse:
    reference_path = PROJECT_ROOT / "samples" / "synthetic" / "reference.pdf"
    candidate_path = PROJECT_ROOT / "samples" / "synthetic" / "tampered_candidate.pdf"
    if not reference_path.is_file() or not candidate_path.is_file():
        raise APIProblem(
            503,
            ErrorDetail(
                code="demo_fixture_unavailable",
                message="The bundled synthetic demo fixture is unavailable.",
            ),
        )
    settings = request.app.state.settings
    try:
        reference = validate_upload(
            field="reference",
            filename=reference_path.name,
            content_type="application/pdf",
            data=reference_path.read_bytes(),
            max_bytes=settings.max_upload_bytes,
        )
        candidate = validate_upload(
            field="candidate",
            filename=candidate_path.name,
            content_type="application/pdf",
            data=candidate_path.read_bytes(),
            max_bytes=settings.max_upload_bytes,
        )
    except (OSError, DocumentValidationError) as exc:
        raise APIProblem(
            503,
            ErrorDetail(
                code="demo_fixture_invalid",
                message="The bundled synthetic demo fixture could not be validated.",
            ),
        ) from exc
    return request.app.state.manager.submit(
        reference, candidate, comparison_mode=ComparisonMode.EXACT
    )


async def _validate_exemplars(
    uploads: list[UploadFile],
    *,
    field: str,
    request: Request,
    minimum: int,
    maximum: int,
) -> tuple[ValidatedUpload, ...]:
    if not uploads:
        return ()
    if not minimum <= len(uploads) <= maximum:
        raise DocumentValidationError(
            "invalid_exemplar_count",
            f"{field} must contain between {minimum} and {maximum} files when provided.",
            field=field,
            details={"count": len(uploads), "minimum": minimum, "maximum": maximum},
        )
    settings = request.app.state.settings
    validated: list[ValidatedUpload] = []
    for index, upload in enumerate(uploads, start=1):
        data = await upload.read(settings.max_upload_bytes + 1)
        exemplar = validate_upload(
            field=f"{field}[{index}]",
            filename=upload.filename,
            content_type=upload.content_type,
            data=data,
            max_bytes=settings.max_upload_bytes,
        )
        if exemplar.page_count > settings.max_pages:
            raise DocumentValidationError(
                "page_limit_exceeded",
                f"Exemplar {index} exceeds the configured page limit.",
                field=f"{field}[{index}]",
                details={"page_count": exemplar.page_count, "max_pages": settings.max_pages},
            )
        validated.append(exemplar)
    return tuple(validated)


def _parse_region_selections(
    raw: str | None,
    *,
    field: str,
) -> tuple[RegionSelection, ...]:
    if raw is None or not raw.strip():
        return ()
    try:
        payload = json.loads(raw)
        if not isinstance(payload, list) or not 1 <= len(payload) <= 10:
            raise ValueError("region selections must be a list containing 1 to 10 entries")
        selections: list[RegionSelection] = []
        for item in payload:
            if not isinstance(item, dict):
                raise ValueError("each region selection must be an object")
            page_number = int(item.get("page_number", item.get("page", 0)))
            box_value = item.get("bounding_box", item.get("box"))
            if box_value is None:
                box_value = {
                    name: item.get(name) for name in ("x", "y", "width", "height")
                }
            if not 1 <= page_number <= 10:
                raise ValueError("page_number must be between 1 and 10")
            selections.append(
                RegionSelection(
                    page_number=page_number,
                    bounding_box=BoundingBox.model_validate(box_value),
                )
            )
        return tuple(selections)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise DocumentValidationError(
            "invalid_region_selection",
            (
                f"{field} must be JSON containing 1 to 10 normalized page regions."
            ),
            field=field,
        ) from exc


@router.get(
    "/analyses/{job_id}",
    response_model=AnalysisJob,
    responses={404: {"model": ErrorResponse}},
    tags=["analysis"],
)
def get_analysis(job_id: str, request: Request) -> AnalysisJob:
    job = request.app.state.store.get_job(job_id)
    if job is None:
        raise _job_not_found()
    if (
        job.candidate_page_url is None
        and request.app.state.store.resolve_asset(job_id, "candidate-page") is not None
    ):
        job = job.model_copy(
            update={
                "candidate_page_url": f"/api/v1/analyses/{job_id}/assets/candidate-page"
            }
        )
    return job


@router.get("/analyses/{job_id}/events", tags=["analysis"])
async def analysis_events(
    job_id: str,
    request: Request,
    last_event_id_header: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    last_event_id_query: Annotated[int | None, Query(alias="last_event_id", ge=0)] = None,
) -> StreamingResponse:
    store = request.app.state.store
    if store.get_job(job_id) is None:
        raise _job_not_found()
    cursor = last_event_id_query or 0
    if last_event_id_header is not None:
        try:
            header_cursor = int(last_event_id_header)
        except ValueError as exc:
            raise APIProblem(
                400,
                ErrorDetail(
                    code="invalid_event_cursor",
                    message="Last-Event-ID must be a non-negative integer.",
                    field="Last-Event-ID",
                ),
            ) from exc
        if header_cursor < 0:
            raise APIProblem(
                400,
                ErrorDetail(
                    code="invalid_event_cursor",
                    message="Last-Event-ID must be a non-negative integer.",
                    field="Last-Event-ID",
                ),
            )
        cursor = max(cursor, header_cursor)

    async def stream() -> AsyncIterator[str]:
        nonlocal cursor
        last_emit = time.monotonic()
        while True:
            events = await asyncio.to_thread(store.get_events_after, job_id, cursor)
            if events:
                for event_type, event in events:
                    cursor = event.event_id
                    yield (
                        f"id: {event.event_id}\n"
                        f"event: {event_type}\n"
                        f"data: {event.model_dump_json()}\n\n"
                    )
                    last_emit = time.monotonic()
                    if event_type in {"complete", "error"}:
                        return
                continue
            if await request.is_disconnected():
                return
            latest = await asyncio.to_thread(store.get_latest_event, job_id)
            if latest is not None:
                latest_type, latest_event = latest
                if latest_type in {"complete", "error"} and cursor >= latest_event.event_id:
                    return
            elif store.get_job(job_id) is None:
                return
            # Completion is announced only by its persisted terminal event. This
            # avoids closing during the tiny state-update/event-append interval.
            await asyncio.to_thread(store.wait_for_change, 1.0)
            if time.monotonic() - last_emit >= 10.0:
                yield ": keep-alive\n\n"
                last_emit = time.monotonic()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/analyses/{job_id}/assets/{asset_id}", tags=["assets"])
def get_asset(job_id: str, asset_id: str, request: Request) -> FileResponse:
    store = request.app.state.store
    if store.get_job(job_id) is None:
        raise _job_not_found()
    resolved = store.resolve_asset(job_id, asset_id)
    if resolved is None:
        raise APIProblem(
            404,
            ErrorDetail(code="asset_not_found", message="Evidence asset not found."),
        )
    path, media_type = resolved
    return FileResponse(
        path,
        media_type=media_type,
        headers={
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _job_not_found() -> APIProblem:
    return APIProblem(
        404,
        ErrorDetail(code="job_not_found", message="Analysis job not found."),
    )


def api_problem_response(problem: APIProblem) -> JSONResponse:
    return JSONResponse(
        status_code=problem.status_code,
        content=json.loads(ErrorResponse(error=problem.error).model_dump_json()),
    )


def _raster_ocr_capability(request: Request) -> tuple[bool, str, str]:
    preference = request.app.state.settings.ocr_provider_preference
    try:
        return raster_ocr_capability(preference)
    except TypeError:
        # Compatibility while upgrading an existing Phase 1 environment.
        return raster_ocr_capability()


def _catalog_profile_summary(profile: DocumentProfile) -> ProfileMatchSummary:
    manifest = profile.manifest
    source = manifest["source"]
    capability = _profile_capability(profile)
    asset_summary = _profile_reference_asset_summary(profile)
    return ProfileMatchSummary(
        profile_id=profile.profile_id,
        issuer=profile.issuer,
        document_family=profile.family,
        subtype=str(manifest["subtype"]),
        provenance_kind=str(manifest["provenance"]["kind"]),
        provenance_assurance=str(manifest["provenance"]["assurance"]),
        score=0.0,
        component_scores={},
        reference_strength="Profile available for local matching",
        explanation=str(manifest["provenance"]["description"]),
        completeness=float(manifest["completeness"]),
        authoritative_source_url=source.get("authoritative_url"),
        visual_reference_available=(
            capability.value in {"visual_reference", "cryptographic"}
            and profile.visual_reference_path is not None
        ),
        display_name=_profile_display_name(manifest),
        document_category=_humanize_profile_value(str(manifest["document_family"])),
        version_label=str(manifest.get("version") or "") or None,
        capability_tier=capability,
        match_level="Weak",
        reference_capability=(
            asset_summary.source_label
            if asset_summary is not None
            else _reference_capability_label(capability)
        ),
        match_reasons=[],
        reference_asset=asset_summary,
        limitations=[str(item) for item in manifest["known_limitations"]],
    )
