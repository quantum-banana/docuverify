from __future__ import annotations

import json

from backend.scripts.export_schemas import MODELS, SCHEMA_DIR, schema_for


def test_committed_json_schemas_match_pydantic_contracts() -> None:
    for name, model in MODELS.items():
        committed = json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
        assert committed == schema_for(name, model), (
            f"Contract drift for {name}; run python -m backend.scripts.export_schemas"
        )
