"""Configuration, from the environment, in one place.

Every setting is SENTINEL_-prefixed so a shared shell does not accidentally
feed us someone else's DATABASE_URL.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SENTINEL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -- database ----------------------------------------------------------
    database_url: str = "postgresql://sentinel:sentinel@localhost:5432/sentinel"
    db_min_pool: int = 2
    db_max_pool: int = 10
    db_command_timeout: float = 30.0

    # -- storage -----------------------------------------------------------
    # Index rows live in the database; the images live here. Keeping them
    # apart is what lets the frame archive grow without the database doing so.
    media_root: Path = Path("./var/media")

    # -- adapters ----------------------------------------------------------
    adapter_dir: Path = Path("./adapters")

    # -- the Sentinel sandbox ---------------------------------------------
    # The catalogue is the contract; the URL pattern is not. We read
    # {base}/api/ingest and take the URLs it gives us.
    sentinel_base_url: str = ""
    sentinel_catalogue_path: str = "/api/ingest"
    sentinel_http_timeout: float = 15.0
    sentinel_token: str = ""
    sentinel_cookie: str = ""

    # -- models ------------------------------------------------------------
    # A missing weights file degrades to a null implementation and a loud log
    # line, rather than a crash. The scaffold has to be runnable before the
    # ONNX exports exist.
    detect_model_path: Path = Path("./var/models/yolo.onnx")
    detect_conf: float = 0.35
    detect_iou: float = 0.55
    detect_classes: list[str] = Field(
        default_factory=lambda: ["car", "motorcycle", "bus", "truck", "auto", "person"]
    )
    onnx_providers: list[str] = Field(default_factory=lambda: ["CPUExecutionProvider"])

    reid_model_path: Path = Path("./var/models/reid.onnx")
    caption_model_path: Path = Path("./var/models/caption.onnx")
    caption_embed_model_path: Path = Path("./var/models/caption_embed.onnx")
    plate_detect_model_path: Path = Path("./var/models/plate_detect.onnx")
    plate_ocr_model_path: Path = Path("./var/models/plate_ocr.onnx")

    embedding_dim: int = 512       # MUST equal sightings.embedding's declared dim
    caption_embedding_dim: int = 384

    # -- ingest ------------------------------------------------------------
    target_decode_fps: float = 10.0     # tracking needs 8-12; the archive needs 1
    archive_fps: float = 1.0

    min_track_frames: int = 3
    reconnect_backoff_initial_s: float = 2.0
    reconnect_backoff_max_s: float = 30.0
    # A PTS jump larger than this means the feed looped or the camera rebooted.
    # Long-lived state has to recover from a hard cut, not assume continuity.
    pts_discontinuity_s: float = 5.0
    max_consecutive_decode_errors: int = 90

    # -- crops -------------------------------------------------------------
    # The crop is the only pixel data a pipeline sees, some extra normal content around is good.
    crop_pad_frac: float = 0.12
    crop_pad_top_two_wheeler_frac: float = 0.70

    # -- queues ------------------------------------------------------------
    queue_claim_batch: int = 8
    queue_lock_timeout_s: float = 300.0   # a lock older than this is reclaimed
    queue_poll_interval_s: float = 0.5
    queue_max_depth: int = 20000          # per crop pipeline, before shedding

    # -- traffic -----------------------------------------------------------
    traffic_bucket_seconds: int = 300

    # -- correlation -------------------------------------------------------
    # Correlation waits for every pipeline the camera expects, then gives up.
    # A stuck violation pipeline must not hold up vehicle matching.
    correlation_timeout_s: float = 120.0
    candidate_limit: int = 500
    knn_limit: int = 200
    max_route_legs: int = 12
    max_routes: int = 200
    max_plausible_speed_kmh: float = 140.0
    min_plausible_speed_kmh: float = 2.0

    # -- logging -----------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = False

    @property
    def crop_dir(self) -> Path:
        return self.media_root / "crops"

    @property
    def frame_dir(self) -> Path:
        return self.media_root / "frames"

    @property
    def evidence_dir(self) -> Path:
        return self.media_root / "evidence"

    @property
    def sentinel_catalogue_url(self) -> str:
        return f"{self.sentinel_base_url.rstrip('/')}{self.sentinel_catalogue_path}"


settings = Settings()
