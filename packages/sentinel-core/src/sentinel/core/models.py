"""Domain models.

These are transport shapes: what an API returns, what a worker passes
around. They are not an ORM and they do not own the schema -- 001_schema.sql
does. Field names match column names exactly, because the design document
refers to columns by name and a rename here would silently make it wrong.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sentinel.core.types import (
    AdapterStatus,
    AlertStatus,
    AlertTier,
    CameraKind,
    LosBand,
    PipelineStatus,
    ResolutionClass,
    ReviewStatus,
    SightingState,
)


class Base(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class Department(Base):
    id: int | None = None
    code: str
    name: str
    created_at: datetime | None = None


class CameraIn(Base):
    camera_id: str
    department_id: int
    name: str
    kind: CameraKind
    vendor: str | None = None
    protocol: str | None = None
    adapter_id: int | None = None
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    bearing_deg: float | None = Field(None, ge=0, lt=360)
    range_m: float | None = Field(None, gt=0)
    storage_kind: str | None = None
    retention_days: int | None = None
    contract_expiry: date | None = None
    enabled: bool = True


class Camera(CameraIn):
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CameraProfileIn(Base):
    """Output of the section 6 survey. This row decides what a camera is
    allowed to claim -- every pipeline gate reads it."""

    resolution_class: ResolutionClass = ResolutionClass.THUMBNAIL
    permitted_attributes: list[str] = Field(default_factory=list)
    permitted_violations: list[str] = Field(default_factory=list)
    plate_viable: bool = False
    density_viable: bool = False
    lane_polygon_wkt: str | None = None
    lane_count: int | None = Field(None, gt=0)
    decode_fps: float | None = None
    deinterlace: bool = False
    distortion: dict[str, Any] | None = None
    trust_level: float = Field(1.0, gt=0, le=1)
    corridor_group: str | None = None
    #: Length of the looping recording behind this feed, in seconds of PTS.
    #: None = unknown. The loop point is not detectable from the video, so
    #: this is how ingest knows to flush tracker state. See 003_loop_period.
    loop_period_s: float | None = Field(None, gt=0)


class CameraProfile(CameraProfileIn):
    camera_id: str
    measured_at: datetime | None = None
    updated_at: datetime | None = None


class Adapter(Base):
    id: int | None = None
    name: str
    driver: str
    config: dict[str, Any] = Field(default_factory=dict)
    status: AdapterStatus = AdapterStatus.REGISTERED
    last_error: str | None = None
    tested_at: datetime | None = None


class HealthCheck(Base):
    """One row per check. 'Reachable but zero detections in six hours' is
    visible here and nowhere else, which is the whole point of the table."""

    camera_id: str
    checked_at: datetime | None = None
    reachable: bool
    last_frame_at: datetime | None = None
    decode_fps: float | None = None
    detections_1h: int | None = None
    verdict: str
    detail: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Sightings
# ---------------------------------------------------------------------------


class SightingStub(Base):
    """What ingestion writes. Everything below crop_ref is filled in later
    by a pipeline, or never, if the camera profile does not permit it."""

    read_id: uuid.UUID | None = None
    camera_id: str
    track_id: str
    seen_at: datetime
    track_start_at: datetime | None = None
    track_end_at: datetime | None = None
    bbox: list[int]
    class_: str = Field(..., alias="class")
    crop_ref: str
    detect_model_id: int | None = None
    describe_status: PipelineStatus = PipelineStatus.PENDING
    embed_status: PipelineStatus = PipelineStatus.PENDING
    plate_status: PipelineStatus = PipelineStatus.PENDING
    violation_status: PipelineStatus = PipelineStatus.PENDING


class Sighting(Base):
    read_id: uuid.UUID
    camera_id: str
    track_id: str
    seen_at: datetime
    track_start_at: datetime | None = None
    track_end_at: datetime | None = None
    bbox: list[int] | None = None
    class_: str | None = Field(None, alias="class")
    crop_ref: str | None = None
    colour: str | None = None
    vtype: str | None = None
    make: str | None = None
    model: str | None = None
    features: list[Any] = Field(default_factory=list)
    caption: str | None = None
    plate_text: str | None = None
    plate_conf: float | None = None
    describe_status: PipelineStatus = PipelineStatus.PENDING
    embed_status: PipelineStatus = PipelineStatus.PENDING
    plate_status: PipelineStatus = PipelineStatus.PENDING
    violation_status: PipelineStatus = PipelineStatus.PENDING
    state: SightingState = SightingState.OPEN
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Rider(Base):
    """A box on a vehicle and nothing more. No embedding, no identity, and
    it cascade-deletes with the sighting. The platform cannot answer 'where
    has this person been', by construction."""

    slot: int = Field(..., ge=1)
    bbox: list[int]
    helmet: bool | None = None       # None = not assessable on this camera
    helmet_conf: float | None = None
    detector_id: int | None = None


class PlateHypothesis(Base):
    rank: int = Field(..., ge=1)
    plate: str
    confidence: float


class Violation(Base):
    id: int | None = None
    read_id: uuid.UUID
    camera_id: str
    violation_type: str
    rider_slot: int | None = None
    confidence: float = Field(..., ge=0, le=1)
    seen_at: datetime
    window_start: datetime | None = None
    window_end: datetime | None = None
    evidence_ref: str | None = None
    evidence_bbox: list[int] | None = None
    detector_id: int | None = None
    review_status: ReviewStatus = ReviewStatus.PENDING_REVIEW


# ---------------------------------------------------------------------------
# Traffic
# ---------------------------------------------------------------------------


class TrafficCount(Base):
    camera_id: str
    bucket_start: datetime
    bucket_seconds: int
    class_: str = Field(..., alias="class")
    vehicle_count: int
    mean_dwell_s: float | None = None


class TrafficState(Base):
    camera_id: str
    bucket_start: datetime
    bucket_seconds: int
    frames_expected: int
    frames_decoded: int
    mean_occupancy: float | None = None
    peak_occupancy: float | None = None
    mean_concurrent: float | None = None
    density_vpkm: float | None = None
    los: LosBand | None = None

    @property
    def coverage(self) -> float:
        """How much of the interval we actually observed. Every count must be
        read against this: a dead camera and a quiet road look identical
        without it."""
        return self.frames_decoded / self.frames_expected if self.frames_expected else 0.0


# ---------------------------------------------------------------------------
# Search, routes, alerts
# ---------------------------------------------------------------------------


class VehicleDescription(Base):
    """What a VAHAN lookup turns a registration number into, and the only
    form the platform can actually search for."""

    registration_no: str | None = None
    colour: str | None = None
    vtype: str | None = None
    make: str | None = None
    model: str | None = None
    district: str | None = None
    caption_query: str | None = None       # free-text, when the operator typed one
    embedding: list[float] | None = None   # reference appearance, when there is one


class Candidate(Base):
    read_id: uuid.UUID
    camera_id: str
    seen_at: datetime
    score: float
    matched_on: list[str] = Field(default_factory=list)   # attribute|vector|caption|plate
    plate_text: str | None = None
    trust_level: float = 1.0


class RouteLeg(Base):
    seq: int
    from_read_id: uuid.UUID
    to_read_id: uuid.UUID
    distance_km: float
    elapsed_s: float
    required_speed_kmh: float
    plausible: bool
    drop_reason: str | None = None
    gap_s: float | None = None


class Route(Base):
    id: uuid.UUID | None = None
    search_id: uuid.UUID | None = None
    rank: int
    score: float
    competing_count: int
    rarity_count: int | None = None
    plate_anchored: bool = False
    min_trust: float | None = None
    legs: list[RouteLeg] = Field(default_factory=list)


class Alert(Base):
    id: uuid.UUID | None = None
    watchlist_entry_id: uuid.UUID | None = None
    read_id: uuid.UUID | None = None
    route_id: uuid.UUID | None = None
    violation_id: int | None = None
    congestion_camera: str | None = None
    congestion_bucket: datetime | None = None
    tier: AlertTier
    score: float
    status: AlertStatus = AlertStatus.NEW
