"""The dashboard API.

Runs on :8001. In the evaluation topology this process also holds correlation
and serves the frontend, which is the three-process-group arrangement the
design describes.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sentinel.api.routers import ROUTERS
from sentinel.core.config import settings
from sentinel.core.db import close_pool, get_pool, healthcheck
from sentinel.core.logging import configure_logging, get_logger

log = get_logger(__name__)

def _find_frontend_dist() -> Path | None:
    """Locate the built frontend by walking up to the repo root.

    Walked rather than hardcoded, because the depth differs between an
    editable workspace install and a wheel, and getting it wrong silently
    serves nothing at all.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "frontend" / "dist"
        if candidate.is_dir():
            return candidate
    return None


FRONTEND_DIST = _find_frontend_dist()

#: Set SENTINEL_EMBED_CORRELATION=1 to run the correlation worker inside this
#: process. Convenient for the evaluation; separate processes in the HLD.
_correlation_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _correlation_task
    configure_logging("api")
    await get_pool()

    import os

    if os.environ.get("SENTINEL_EMBED_CORRELATION") == "1":
        from sentinel.correlation.worker import CorrelationWorker

        worker = CorrelationWorker()
        _correlation_task = asyncio.create_task(worker.run(), name="correlation")
        log.info("correlation_embedded")

    log.info("api_started")
    yield

    if _correlation_task is not None:
        _correlation_task.cancel()
    await close_pool()


app = FastAPI(
    title="Sentinel Dashboard API",
    version="0.1.0",
    description=(
        "Map, search, routes, alerts, violations, traffic and evidence for the "
        "integrated video management and analytics platform."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

for router in ROUTERS:
    app.include_router(router)


@app.get("/healthz", tags=["ops"])
async def healthz():
    return {"ok": await healthcheck(), "service": "api"}


@app.get("/config", tags=["ops"])
async def client_config():
    """What the frontend needs to know about this deployment."""
    return {
        "traffic_bucket_seconds": settings.traffic_bucket_seconds,
        "coverage_floor": 0.6,
        "max_routes": settings.max_routes,
    }


if FRONTEND_DIST is not None:
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
else:   # pragma: no cover
    log.info("frontend_not_built", hint="run npm install && npm run build in frontend/")
