"""Discovering, testing and registering adapters.

The platform finds an adapter, tests it, and either registers it or reports
the failure without crashing. That last clause is the important one: a
department's adapter written badly at 2am must produce a row with
status='failed' and a readable last_error, not a dead ingest process.

Layout of a drop-in adapter:

    adapters/
      my-vms/
        adapter.toml        name, driver, and driver-specific config

The driver is either a dotted module path ("sentinel.ingest.adapters.builtin.rtsp")
or "./driver.py" relative to the adapter folder.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import tomllib
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sentinel.core.config import settings
from sentinel.core.logging import get_logger
from sentinel.core.types import AdapterStatus
from sentinel.ingest.adapters.base import Adapter

log = get_logger(__name__)


@dataclass
class LoadedAdapter:
    name: str
    driver: str
    config: dict[str, Any]
    status: AdapterStatus
    adapter: Adapter | None = None
    last_error: str | None = None
    camera_count: int = 0


def _import_driver(driver: str, folder: Path):
    """Import by dotted path, or by file path relative to the adapter folder."""
    if driver.endswith(".py") or driver.startswith("."):
        path = (folder / driver).resolve()
        if not path.exists():
            raise FileNotFoundError(f"driver file not found: {path}")
        spec = importlib.util.spec_from_file_location(f"sentinel_adapter_{folder.name}", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    return importlib.import_module(driver)


def _self_test(adapter: Adapter) -> int:
    """Exercise the mandatory methods before letting an adapter register.

    enumerate() must return cameras. open() must give a stream handle for the
    first of them. health() must answer. A driver that passes this is not
    proven correct, but a driver that fails it will fail at 3am instead.
    """
    cameras = adapter.enumerate()
    if not isinstance(cameras, list):
        raise TypeError("enumerate() must return a list of CameraRef")
    if not cameras:
        raise ValueError("enumerate() returned no cameras")

    probe = cameras[0].camera_id
    handle = adapter.open(probe)
    if not getattr(handle, "url", None):
        raise ValueError(f"open({probe!r}) returned no stream url")
    if handle.transport.lower() != "tcp" and handle.url.startswith("rtsp"):
        raise ValueError("RTSP adapters must force TCP transport")

    report = adapter.health(probe)
    if not hasattr(report, "reachable"):
        raise TypeError("health() must return a HealthReport")

    return len(cameras)


def load_one(folder: Path) -> LoadedAdapter:
    """Load and test a single adapter folder. Never raises."""
    manifest = folder / "adapter.toml"
    name = folder.name
    driver = ""
    config: dict[str, Any] = {}

    try:
        if not manifest.exists():
            raise FileNotFoundError("adapter.toml missing")
        config = tomllib.loads(manifest.read_text())
        name = str(config.get("name") or folder.name)
        driver = str(config.get("driver") or "")
        if not driver:
            raise ValueError("adapter.toml has no 'driver'")

        module = _import_driver(driver, folder)
        if not hasattr(module, "build"):
            raise AttributeError(f"{driver} has no build(config) function")

        adapter = module.build(config)
        count = _self_test(adapter)

        log.info("adapter_registered", adapter=name, driver=driver, cameras=count)
        return LoadedAdapter(
            name=name, driver=driver, config=config, status=AdapterStatus.REGISTERED,
            adapter=adapter, camera_count=count,
        )

    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        log.error(
            "adapter_failed", adapter=name, driver=driver, error=detail,
            trace=traceback.format_exc(limit=4),
        )
        return LoadedAdapter(
            name=name, driver=driver, config=config,
            status=AdapterStatus.FAILED, last_error=detail,
        )


def discover(adapter_dir: Path | None = None) -> list[LoadedAdapter]:
    """Load every adapter folder. Failures are results, not exceptions."""
    root = Path(adapter_dir or settings.adapter_dir)
    if not root.exists():
        log.warning("adapter_dir_missing", path=str(root))
        return []

    results = [
        load_one(child)
        for child in sorted(root.iterdir())
        if child.is_dir() and not child.name.startswith((".", "_"))
    ]
    ok = sum(1 for r in results if r.status is AdapterStatus.REGISTERED)
    log.info("adapters_discovered", total=len(results), registered=ok, failed=len(results) - ok)
    return results


async def sync_to_registry(loaded: list[LoadedAdapter]) -> None:
    """Write what the loader found into the adapters table.

    The config is stored without any driver-supplied secrets stripped, so
    keep credentials in the environment and refer to them by name in
    adapter.toml.
    """
    from sentinel.core.db import transaction
    from sentinel.core.models import Adapter as AdapterRow

    async with transaction() as conn:
        for item in loaded:
            row = AdapterRow(
                name=item.name, driver=item.driver, config=item.config,
                status=item.status, last_error=item.last_error,
            )
            await conn.execute(
                """
                INSERT INTO adapters (name, driver, config, status, last_error, tested_at)
                VALUES ($1, $2, $3, $4, $5, now())
                ON CONFLICT (name) DO UPDATE SET
                    driver = EXCLUDED.driver, config = EXCLUDED.config,
                    status = EXCLUDED.status, last_error = EXCLUDED.last_error,
                    tested_at = now()
                """,
                row.name, row.driver, row.config, row.status.value, row.last_error,
            )
