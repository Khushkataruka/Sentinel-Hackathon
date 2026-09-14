"""Demand, bounded retention and frame/track alignment for delayed playback."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sentinel.api.deps import current_user, db_conn
from sentinel.api.routers import annotated
from sentinel.core.config import settings
from sentinel.ingest import live
from sentinel.ingest.annotate import Label
from sentinel.ingest.decode import Frame
from sentinel.ingest.live import FrameBuffer, LivePublisher
from sentinel.ingest.track import TrackState
from sentinel.ingest.worker import CameraWorker


@pytest.fixture
def buffer(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "media_root", tmp_path)
    monkeypatch.setattr(settings, "annotated_feed_fps", 5)
    monkeypatch.setattr(settings, "annotated_feed_delay_s", 60)
    monkeypatch.setattr(settings, "annotated_feed_enabled", True)
    monkeypatch.setattr(settings, "annotated_feed_width", 320)
    return FrameBuffer("CAM-1")


def packet(at, *, session="run-1", started_at=100):
    return {
        "received_at": at,
        "seen_at": datetime.fromtimestamp(at, UTC).isoformat(),
        "started_at": started_at,
        "session": session,
        "boxes": [{"t": f"{session}:0:1", "b": [40, 40, 100, 100], "c": "car"}],
        "detector_unavailable": False,
    }


def test_waits_full_delay_and_pairs_exact_image_with_track(buffer):
    buffer.write(packet(100.15), b"first frame")
    buffer.write(packet(100.35), b"second frame")
    buffer.write(packet(159), b"latest")
    assert buffer.read(159)[1] is None
    metadata, jpeg = buffer.read(160.2)
    assert jpeg == b"first frame"
    assert metadata["received_at"] == 100.15
    assert metadata["boxes"][0]["t"] == "run-1:0:1"
    assert buffer.read(160.4)[1] == b"second frame"


def test_restarted_worker_cannot_replay_previous_session(buffer):
    buffer.write(packet(100), b"old worker")
    buffer.write(packet(160, session="new", started_at=160), b"new worker")
    state, jpeg = buffer.read(160)
    assert jpeg is None
    assert state["state"] == "buffering"
    assert state["wait_s"] == 60


def test_no_frozen_playback_after_ingest_stops(buffer):
    buffer.write(packet(100), b"frame")
    assert buffer.read(160)[0]["state"] == "stalled"


def test_storage_is_bounded_and_overwritten_slots_are_not_replayed(buffer):
    for n in range(buffer.slots * 3):
        buffer.write(packet(100 + n / buffer.fps), str(n).encode())
    assert len(list(buffer.directory.glob("*.frame"))) == buffer.slots
    # Reading an old timestamp must not return newer data from its reused slot.
    assert buffer.read(160)[1] is None


def test_camera_ids_cannot_escape_storage_root(buffer, tmp_path):
    malicious = FrameBuffer("../../outside")
    assert malicious.directory.parent == tmp_path / "annotated"
    assert len(malicious.directory.name) == 64


def frame_and_tracks():
    frame = Frame(np.zeros((240, 640, 3), np.uint8), datetime.now(UTC), 1, 0.1, 1)
    tracks = [
        SimpleNamespace(state=TrackState.CONFIRMED, cls=cls, bbox=(80, 40, 240, 120), score=0.9)
        for cls in ("car", "person")
    ]
    worker = SimpleNamespace(scoped_track_id=lambda track: "run:0:1", detector=lambda img: [])
    return frame, tracks, worker


def test_no_encoding_without_viewer_and_cleanup_after_last_viewer(buffer, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(live.time, "time", lambda: now[0])
    publisher = LivePublisher("CAM-1")
    frame, tracks, worker = frame_and_tracks()
    publisher.publish(frame, tracks, worker)
    assert not buffer.directory.exists()

    buffer.request_view()
    now[0] = 101
    publisher.publish(frame, tracks, worker)
    metadata = json.loads((buffer.directory / "latest.json").read_bytes())
    assert metadata["boxes"] == [{"t": "run:0:1", "b": [40, 20, 120, 60], "c": "car", "s": 0.9}]
    assert len(list(buffer.directory.glob("*.frame"))) == 1

    now[0] = 116
    publisher.publish(frame, tracks, worker)
    assert not list(buffer.directory.glob("*.frame"))
    assert not (buffer.directory / "latest.json").exists()
    assert publisher.started_at is None


def test_second_viewer_keeps_shared_capture_active(buffer, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(live.time, "time", lambda: now[0])
    buffer.request_view()
    now[0] = 110
    FrameBuffer("CAM-1").request_view()
    assert buffer.requested(120)
    assert not buffer.requested(126)


def test_offline_worker_does_not_publish(buffer):
    assert CameraWorker("CAM-1", None, detector=lambda img: [], once=True).publisher is None


def test_renderer_draws_boxes_on_their_own_frame():
    image = np.zeros((200, 300, 3), np.uint8)
    _, jpeg = cv2.imencode(".jpg", image)
    labels = {"run-1:0:1": Label("run-1:0:1", lines=["white car"], violations=["no_helmet"])}
    rendered = annotated.render_frame(jpeg.tobytes(), packet(100), labels)
    result = cv2.imdecode(np.frombuffer(rendered, np.uint8), cv2.IMREAD_COLOR)
    # The box is red for a violation; unrelated bottom-right road stays black.
    assert result[70, 40, 2] > 150
    assert result[180, 280].max() < 10


@pytest.fixture
def client(buffer, monkeypatch):
    class Connection:
        allowed = True

        async def fetchrow(self, query, camera_id, departments):
            assert camera_id == "CAM-1"
            assert departments == [7]
            return {"enabled": True} if self.allowed else None

    conn = Connection()

    async def connection():
        yield conn

    async def user():
        return SimpleNamespace(id="user-1")

    async def departments(conn, user_id):
        assert user_id == "user-1"
        return [7]

    monkeypatch.setattr(annotated, "visible_departments", departments)
    app = FastAPI()
    app.include_router(annotated.router)
    app.dependency_overrides[db_conn] = connection
    app.dependency_overrides[current_user] = user
    with TestClient(app) as http:
        yield http, conn


def test_access_is_checked_before_starting_capture(client, buffer):
    http, conn = client
    conn.allowed = False
    assert http.get("/annotated/CAM-1/frame").status_code == 404
    assert not buffer.directory.exists()


def test_missing_ingest_reports_waiting_and_renews_lease(client, buffer):
    response = client[0].get("/annotated/CAM-1/frame")
    assert response.status_code == 202
    assert response.json()["state"] == "waiting"
    assert response.headers["cache-control"] == "no-store"
    assert buffer.requested(live.time.time())


def test_api_joins_exact_scoped_ids_and_exposes_pending_and_delay(client, buffer, monkeypatch):
    monkeypatch.setattr(live.time, "time", lambda: 160)
    _, jpeg = cv2.imencode(".jpg", np.zeros((200, 300, 3), np.uint8))
    buffer.write(packet(100), jpeg.tobytes())
    buffer.write(packet(159), jpeg.tobytes())

    async def labels(camera_id, track_ids, *, conn):
        assert camera_id == "CAM-1"
        assert track_ids == ["run-1:0:1"]
        assert conn is client[1]
        return {}

    async def model_note(*, conn):
        return "STUB MODELS: describe"

    monkeypatch.setattr(annotated, "load_labels", labels)
    monkeypatch.setattr(annotated, "stub_note", model_note)
    response = client[0].get("/annotated/CAM-1/frame")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["x-pending-tracks"] == "1"
    assert response.headers["x-frame-delay"] == "60"
    assert response.headers["x-frame-seen-at"] == packet(100)["seen_at"]
    assert response.headers["x-model-note"] == "STUB MODELS: describe"
    assert cv2.imdecode(np.frombuffer(response.content, np.uint8), cv2.IMREAD_COLOR) is not None
