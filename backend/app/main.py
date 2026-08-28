"""FastAPI application factory and default ASGI application."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.app import __version__
from backend.app.api.v1 import APIProblem, api_problem_response, router
from backend.app.core.config import Settings
from backend.app.core.storage import JobStore
from backend.app.docuvault import ProfileRepository
from backend.app.models.contracts import ErrorDetail, ErrorResponse
from backend.app.services.pipeline import AnalysisManager


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or Settings()
    store = JobStore(active_settings.runtime_dir, active_settings.database_path)
    profiles = ProfileRepository(
        bundled_root=active_settings.bundled_profiles_path,
        schema_path=active_settings.profile_schema_path,
        index_path=active_settings.profile_index_path,
        project_root=active_settings.bundled_profiles_path.parents[2],
        external_root=active_settings.docuvault_path,
    )
    manager = AnalysisManager(active_settings, store, profiles=profiles)

    async def cleanup_expired_jobs() -> None:
        while True:
            await asyncio.sleep(active_settings.cleanup_interval_seconds)
            await asyncio.to_thread(
                store.cleanup_expired, active_settings.retention_hours
            )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        store.startup()
        store.cleanup_expired(active_settings.retention_hours)
        profiles.startup()
        cleanup_task = asyncio.create_task(cleanup_expired_jobs())
        try:
            yield
        finally:
            cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup_task
            manager.shutdown()

    application = FastAPI(
        title="DocuVerify API",
        description="Explainable exact and template comparison for 1-10 page documents.",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    application.state.settings = active_settings
    application.state.store = store
    application.state.manager = manager
    application.state.profiles = profiles
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(active_settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Content-Type", "Last-Event-ID"],
    )

    @application.exception_handler(APIProblem)
    async def handle_api_problem(_: Request, problem: APIProblem) -> JSONResponse:
        return api_problem_response(problem)

    @application.exception_handler(RequestValidationError)
    async def handle_request_validation(
        _: Request, error: RequestValidationError
    ) -> JSONResponse:
        fields = [
            ".".join(str(part) for part in item.get("loc", ()) if part not in {"body", "query"})
            for item in error.errors()
        ]
        detail = ErrorDetail(
            code="invalid_request",
            message="The request is missing required fields or contains invalid values.",
            details={"fields": [field for field in fields if field]},
        )
        return JSONResponse(
            status_code=422,
            content=ErrorResponse(error=detail).model_dump(mode="json"),
        )

    application.include_router(router)
    return application


app = create_app()
