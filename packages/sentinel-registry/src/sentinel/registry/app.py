"""The registry service.

Runs on :8000. Owns who exists, what cameras exist, and what each camera is
allowed to claim.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sentinel.core.db import close_pool, get_pool, healthcheck
from sentinel.core.logging import configure_logging, get_logger
from sentinel.registry.routers import ROUTERS

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging("registry")
    await get_pool()
    log.info("registry_started")
    yield
    await close_pool()


app = FastAPI(
    title="Sentinel Registry",
    version="0.1.0",
    description=(
        "Camera registry, capability profiles, adapters and health for the "
        "integrated video management and analytics platform."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # tightened per-deployment; the HLD covers this
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in ROUTERS:
    app.include_router(router)


@app.get("/healthz", tags=["ops"])
async def healthz():
    ok = await healthcheck()
    return {"ok": ok, "service": "registry"}
