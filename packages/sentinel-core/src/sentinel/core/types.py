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


# ---------------------------------------------------------------------------
# Default capability grant
# ---------------------------------------------------------------------------
#
# Every camera is granted everything by default. This deliberately replaces
# the "nothing until surveyed" rule the registry started with: a camera now
# claims the full attribute set, ANPR and every violation type from the
# moment it is onboarded, without a section 6 survey.
#
# Consequences worth knowing, since nothing downstream re-checks them:
#   - `red_light` and `wrong_way` need a stop line and lane geometry. No
#     camera in the estate has `lane_polygon` or `lane_count`, so these fire
#     on scene context that was never measured.
#   - `no_seatbelt` and `phone_use` need glass penetration at pole distance.
#     They have no auto_confirm_threshold, so they still route to a human.
#   - DENSITY_VIABLE_BY_DEFAULT does not conjure lane geometry: traffic.py
#     still needs lane_length_m before it emits density_vpkm.
#
# ALL_VIOLATIONS must stay equal to the enabled rows in `violation_types`.
# test_default_permissions.py checks that against a live database.

#: Every field `Description.restrict_to` can keep. 'type' is the profile's
#: spelling of the sightings column `vtype`.
ALL_ATTRIBUTES: list[str] = ["colour", "type", "make", "model", "features"]

#: Every `violation_types.code` seeded by 001_schema.sql.
ALL_VIOLATIONS: list[str] = [
    "illegal_parking",
    "no_helmet",
    "no_seatbelt",
    "phone_use",
    "red_light",
    "triple_riding",
    "wrong_way",
]

PLATE_VIABLE_BY_DEFAULT = True
DENSITY_VIABLE_BY_DEFAULT = True
