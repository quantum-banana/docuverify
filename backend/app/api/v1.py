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
    CapabilityStatus,
    CreateAnalysisResponse,
    DiagnosticsResponse,
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
)
from backend.app.services.documents import DocumentValidationError, validate_upload


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
            raster_ocr=False,
            sse=True,
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
    return DiagnosticsResponse(
        python_version=platform.python_version(),
        ocr_provider="pymupdf_embedded_text",
        ocr_device="cpu",
        opencv_version=cv2.__version__,
        pymupdf_version=str(getattr(fitz, "VersionBind", fitz.version[0])),
        numpy_version=np.__version__,
        gpu_detected=gpu_detected,
        backend_ready=runtime_writable,
        runtime_writable=runtime_writable,
    )


@router.post(
    "/analyses/reference",
    response_model=CreateAnalysisResponse,
    status_code=202,
    responses={400: {"model": ErrorResponse}, 413: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    tags=["analysis"],
)
async def create_reference_analysis(
    request: Request,
    reference: Annotated[UploadFile, File(description="Trusted one-page reference")],
    candidate: Annotated[UploadFile, File(description="Questioned one-page document")],
    comparison_mode: Annotated[str, Form()] = "exact",
) -> CreateAnalysisResponse:
    if comparison_mode != "exact":
        raise APIProblem(
            422,
            ErrorDetail(
                code="unsupported_comparison_mode",
                message="Phase 1 supports only comparison_mode=exact.",
                field="comparison_mode",
                details={"supported": ["exact"]},
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
    return request.app.state.manager.submit(validated_reference, validated_candidate)


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
    return request.app.state.manager.submit(reference, candidate)


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
    if request.app.state.store.resolve_asset(job_id, "candidate-page") is not None:
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
