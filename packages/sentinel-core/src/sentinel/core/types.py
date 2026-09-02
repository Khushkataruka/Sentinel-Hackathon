"""Python mirrors of the enum types in 001_schema.sql.

They exist so a typo is an ImportError at start-up rather than a
constraint violation at three in the morning. If you add a value to an
enum in the schema, add it here too; test_types.py checks the two agree
against a live database.
"""

from __future__ import annotations

from enum import StrEnum


class CameraKind(StrEnum):
    IP = "ip"
    ANALOG = "analog"


class AdapterStatus(StrEnum):
    REGISTERED = "registered"
    FAILED = "failed"
    DISABLED = "disabled"


class PipelineStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"


class SightingState(StrEnum):
    OPEN = "open"
    COMPLETE = "complete"
    CORRELATED = "correlated"


class SearchKind(StrEnum):
    REGISTRATION = "registration"
    DESCRIPTION = "description"
    WATCHLIST_BACKFILL = "watchlist_backfill"


class AlertTier(StrEnum):
    DISMISS = "dismiss"
    REVIEW = "review"
    PRIORITY = "priority"


class AlertStatus(StrEnum):
    NEW = "new"
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"


class ActorKind(StrEnum):
    USER = "user"
    SERVICE = "service"


class ReviewStatus(StrEnum):
    AUTO_CONFIRMED = "auto_confirmed"
    PENDING_REVIEW = "pending_review"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class LosBand(StrEnum):
    FREE = "free"
    LIGHT = "light"
    MODERATE = "moderate"
    HEAVY = "heavy"
    JAM = "jam"


class Pipeline(StrEnum):
    """pipeline_kind, from 002_pipeline_jobs.sql."""

    DESCRIBE = "describe"
    EMBED = "embed"
    PLATE = "plate"
    VIOLATE = "violate"


class ModelRole(StrEnum):
    DETECT = "detect"
    TRACK = "track"
    DESCRIBE = "describe"
    EMBED = "embed"
    CAPTION_EMBED = "caption_embed"
    PLATE_DETECT = "plate_detect"
    PLATE_OCR = "plate_ocr"
    VIOLATION = "violation"


class UserRole(StrEnum):
    OPERATOR = "operator"
    ADMIN = "admin"
    AUDITOR = "auditor"


class ResolutionClass(StrEnum):
    FULL = "full"
    REDUCED = "reduced"
    THUMBNAIL = "thumbnail"


#: sightings.<pipeline>_status column names, keyed by pipeline.
STATUS_COLUMN: dict[Pipeline, str] = {
    Pipeline.DESCRIBE: "describe_status",
    Pipeline.EMBED: "embed_status",
    Pipeline.PLATE: "plate_status",
    Pipeline.VIOLATE: "violation_status",
}

#: sighting_events.event values a pipeline posts when it finishes.
DONE_EVENT: dict[Pipeline, str] = {
    Pipeline.DESCRIBE: "describe_done",
    Pipeline.EMBED: "embed_done",
    Pipeline.PLATE: "plate_done",
    Pipeline.VIOLATE: "violation_done",
}
