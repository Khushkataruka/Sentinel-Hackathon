"""The adapter contract.

An adapter is a config file plus a driver, dropped into a folder. Fifty
different cameras means writing three or four adapters, not fifty
integrations -- which is the entire reason the platform can claim to be
vendor-neutral.

Four methods, three of them mandatory. seek() is optional and a driver
declares whether it has one; nothing in the platform depends on it, because
the frame archive exists precisely so that reviewing footage does not
require a vendor to support seeking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass
class CameraRef:
    """A camera as the adapter knows it, before the registry has an opinion."""

    camera_id: str
    name: str
    url: str
    lat: float | None = None
    lon: float | None = None
    codec: str | None = None
    width: int | None = None
    height: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StreamHandle:
    """What open() returns: enough to construct a capture, and nothing more.

    transport is forced to TCP for RTSP by every built-in driver. UDP is
    accepted by the sandbox but fails across NAT and most corporate
    firewalls, and partial UDP delivery produces corrupt frames that look
    exactly like model bugs.
    """

    camera_id: str
    url: str
    transport: str = "tcp"
    codec: str | None = None
    options: dict[str, str] = field(default_factory=dict)


@dataclass
class HealthReport:
    reachable: bool
    last_frame_at: datetime | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Adapter(Protocol):
    """What a driver module must provide.

    A driver module exposes `build(config: dict) -> Adapter`. The loader
    calls it, then exercises the three mandatory methods before the adapter
    is allowed to register.
    """

    name: str
    supports_seek: bool

    def enumerate(self) -> list[CameraRef]:
        """The cameras this adapter's config claims."""
        ...

    def open(self, camera_id: str) -> StreamHandle:
        """A stream we can decode, within a timeout."""
        ...

    def health(self, camera_id: str) -> HealthReport:
        """Reachability and last frame time."""
        ...

    def seek(self, camera_id: str, ts: datetime) -> StreamHandle:
        """Frames at the requested time. Optional."""
        ...


class AdapterError(RuntimeError):
    """Raised by a driver for anything the loader should record as
    last_error rather than crash on."""


class BaseAdapter:
    """Convenience base. Drivers may ignore it and satisfy the Protocol
    directly."""

    name = "unnamed"
    supports_seek = False

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.name = str(config.get("name", self.name))

    def enumerate(self) -> list[CameraRef]:   # pragma: no cover - abstract
        raise NotImplementedError

    def open(self, camera_id: str) -> StreamHandle:   # pragma: no cover - abstract
        raise NotImplementedError

    def health(self, camera_id: str) -> HealthReport:   # pragma: no cover - abstract
        raise NotImplementedError

    def seek(self, camera_id: str, ts: datetime) -> StreamHandle:
        raise AdapterError(f"{self.name} does not support seeking")
