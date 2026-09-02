"""Where images go.

The database holds the index; the disk holds the pixels. Paths are
deterministic and camera/date partitioned so a directory listing stays a
sane size at 80,000 cameras and a retention sweep is a directory delete
rather than a query.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from sentinel.core.config import settings


def _partition(root: Path, camera_id: str, when: datetime) -> Path:
    safe = camera_id.replace("/", "_")
    return root / safe / when.strftime("%Y-%m-%d") / when.strftime("%H")


def crop_path(camera_id: str, when: datetime, read_id: uuid.UUID | str) -> Path:
    """Best-frame crop for one sighting. Stored in sightings.crop_ref."""
    d = _partition(settings.crop_dir, camera_id, when)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{read_id}.jpg"


def frame_path(camera_id: str, when: datetime) -> Path:
    """One archived frame. Stored in frames.path.

    The archive is 1 fps, so the second is a unique key per camera -- which
    is exactly the UNIQUE (camera_id, captured_at) the schema declares.
    """
    d = _partition(settings.frame_dir, camera_id, when)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{when.strftime('%Y%m%dT%H%M%S')}.jpg"


def evidence_path(camera_id: str, when: datetime, key: str) -> Path:
    """Annotated frame behind a violation. Stored in violations.evidence_ref."""
    d = _partition(settings.evidence_dir, camera_id, when)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key}.jpg"


def relative(path: Path | str) -> str:
    """Store paths relative to media_root, so the archive can be moved."""
    p = Path(path)
    try:
        return str(p.relative_to(settings.media_root.resolve()))
    except ValueError:
        try:
            return str(p.relative_to(settings.media_root))
        except ValueError:
            return str(p)


def absolute(ref: str) -> Path:
    p = Path(ref)
    return p if p.is_absolute() else settings.media_root / p
