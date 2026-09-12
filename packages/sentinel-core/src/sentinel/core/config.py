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
    # {base}/cameras.json and take the URLs it gives us.
    sentinel_base_url: str = ""
    sentinel_catalogue_path: str = "/cameras.json"
    #: Tried in order after catalogue_path 404s. The grid moved from
    #: /api/ingest to /cameras.json; both estates exist in the wild and a
    #: sync that dies on a renamed path is a bad trade for one list entry.
    sentinel_catalogue_fallback_paths: list[str] = Field(
        default_factory=lambda: ["/api/ingest"]
    )
    sentinel_http_timeout: float = 15.0
    sentinel_token: str = ""
    sentinel_cookie: str = ""

    # -- grid media plane --------------------------------------------------
    # HLS comes off the CDN host in sentinel_base_url. RTSP and WebRTC cannot
    # be proxied by a CDN, so they are served from the gateway's own address
    # and authenticate every connection with the registered email and access
    # password embedded in the URL. See sentinel.core.streamurl: the '@' in
    # the email has to be percent-encoded, and a credentialed URL must never
    # reach a log line, the adapters table or an API response.
    grid_email: str = ""
    grid_password: str = ""
    #: Where RTSP and WHEP actually live. Empty means the catalogue's own
    #: hostnames are already correct and should be left alone.
    grid_media_host: str = "103.250.160.189"
    grid_rtsp_port: int = 8554
    grid_whep_port: int = 8889
    #: Decode HLS off the CDN instead of RTSP off the gateway. For a machine
    #: that cannot reach 8554/TCP -- the guide's own fallback. Costs latency
    #: and a segment of buffering, so it is not the default.
    grid_prefer_hls: bool = False

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

    #: Rider/helmet and phone detectors. Ultralytics .pt, run over the vehicle
    #: crop rather than the frame -- see pipelines/models/violations.py.
    helmet_model_path: Path = Path("./var/models/helmet_merged_yolo11m_best.pt")
    phone_model_path: Path = Path("./var/models/phone_v2_yolo11m_best.pt")
    helmet_conf: float = 0.35
    phone_conf: float = 0.30
    #: 'cpu', 'mps', or a CUDA index as a string. See the throughput note in
    #: pipelines/models/violations.py before changing it.
    violation_device: str = "cpu"
    #: Longest crop side, in pixels, below which no violation is assessable.
    #: A helmet is a fraction of a rider box; under roughly this the weights
    #: (trained at 640) have nothing to resolve. The 480x360 sample footage
    #: sits entirely below it, which is why that run reports skipped.
    violation_min_crop_px: int = 96

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

    def sentinel_catalogue_urls(self, base_url: str | None = None) -> list[str]:
        """Every catalogue URL to try, in order, deduplicated."""
        base = (base_url or self.sentinel_base_url).rstrip("/")
        urls: list[str] = []
        for path in [self.sentinel_catalogue_path, *self.sentinel_catalogue_fallback_paths]:
            url = f"{base}/{path.lstrip('/')}"
            if url not in urls:
                urls.append(url)
        return urls


settings = Settings()
