"""Export canonical JSON Schemas from the backend Pydantic contracts.

Run from the repository root with ``python -m backend.scripts.export_schemas``.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from backend.app.models.contracts import (
    AnalysisJob,
    CreateAnalysisResponse,
    DiagnosticsResponse,
    DocumentResult,
    ErrorResponse,
    Finding,
    HealthResponse,
    PageResult,
    ProgressEvent,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = PROJECT_ROOT / "shared" / "schemas"
MODELS: dict[str, type[BaseModel]] = {
    "analysis-job": AnalysisJob,
    "create-analysis-response": CreateAnalysisResponse,
    "diagnostics": DiagnosticsResponse,
    "document-result": DocumentResult,
    "error-response": ErrorResponse,
    "finding": Finding,
    "health": HealthResponse,
    "page-result": PageResult,
    "progress-event": ProgressEvent,
}


def schema_for(name: str, model: type[BaseModel]) -> dict:
    schema = model.model_json_schema(mode="serialization")
    schema["$id"] = f"https://docuverify.local/schemas/v1/{name}.schema.json"
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return schema


def main() -> None:
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    for name, model in MODELS.items():
        path = SCHEMA_DIR / f"{name}.schema.json"
        path.write_text(
            json.dumps(schema_for(name, model), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
