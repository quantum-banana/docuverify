"""Convenient Windows-friendly backend entry point."""

from __future__ import annotations

import os

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "backend.app.main:app",
        host=os.getenv("DOCUVERIFY_BACKEND_HOST", "127.0.0.1"),
        port=int(os.getenv("DOCUVERIFY_BACKEND_PORT", "8000")),
        reload=False,
    )
