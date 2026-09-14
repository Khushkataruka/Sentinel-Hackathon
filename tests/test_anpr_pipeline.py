"""Comprehensive test suite for the ANPR pipeline and models."""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import cv2
import numpy as np
import pytest
from sentinel.core import queue
from sentinel.core.types import Pipeline, PipelineStatus
from sentinel.pipelines.models import anpr
from sentinel.pipelines.plate import PlateWorker


def make_test_crop(w: int = 128, h: int = 96, seed: int = 42) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 255, (h, w, 3), dtype=np.uint8)


def test_plate_validation_valid_formats():
    # Modern Indian format
    assert anpr.validate("GJ01AB1234")
    assert anpr.validate("MH12DE5678")
    assert anpr.validate("DL03CAA1234")
    assert anpr.validate("KA05MB9999")
    assert anpr.validate("TN09AZ0001")
    # Older/Short forms
    assert anpr.validate("GJ1A1234")
    assert anpr.validate("DL1C9999")
    # Bharat Series
    assert anpr.validate("22BH1234AA")
    assert anpr.validate("21BH9876A")
    # Space / punctuation stripping
    assert anpr.validate("GJ-01-AB-1234")
    assert anpr.validate("MH 12 DE 5678")


def test_plate_validation_invalid_formats():
    # Non-existent state codes
    assert not anpr.validate("XX01AB1234")
    assert not anpr.validate("ZZ99ZZ9999")
    # Length too short
    assert not anpr.validate("GJ01")
    assert not anpr.validate("ABC")
    # Garbage characters
    assert not anpr.validate("1234567890")
    assert not anpr.validate("GJ01AB1234XYZ")


def test_enable_validation_global_flag_toggle():
    original_val = anpr.ENABLE_VALIDATION
    try:
        anpr.ENABLE_VALIDATION = False
        # When validation is turned off, raw non-conforming or foreign plates return True
        assert anpr.validate("RAW_TEXT_123")
        assert anpr.validate("CUSTOM-PLATE")
        assert anpr.validate("NY-492-ABC")
        assert not anpr.validate("")  # empty still false

        anpr.ENABLE_VALIDATION = True
        assert not anpr.validate("RAW_TEXT_123")
        assert not anpr.validate("CUSTOM-PLATE")
    finally:
        anpr.ENABLE_VALIDATION = original_val


def test_stub_plate_reader_structure():
    reader = anpr.StubPlateReader()
    assert reader.is_stub is True

    crop = make_test_crop(seed=123)
    res1 = reader(crop, top_k=3)
    res2 = reader(crop, top_k=3)
    assert len(res1) == len(res2)
    if res1:
        assert res1[0].text == res2[0].text
        assert res1[0].confidence == res2[0].confidence
        assert res1[0].rank == 1
        assert res1[0].valid_format is True
        assert len(res1) <= 3


def test_process_anpr_frame_artifact_saving():
    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = Path(tmpdir) / "annotated_subdir" / "frame_001.jpg"
        frame = make_test_crop(w=640, h=480, seed=1)

        mock_reader = MagicMock()
        mock_reader.process_frame.return_value = [
            {
                "vehicle_bbox": [50, 50, 200, 200],
                "crop_bbox": [40, 40, 210, 210],
                "class": "car",
                "vehicle_conf": 0.92,
                "plate_detected": True,
                "plate_bbox": [100, 150, 180, 175],
                "plate_text": "GJ01AB1234",
                "plate_confidence": 0.88,
                "valid_format": True,
                "hypotheses": [
                    {"rank": 1, "plate": "GJ01AB1234", "confidence": 0.88, "valid_format": True}
                ],
            }
        ]

        annotated, results = anpr.process_anpr_frame(
            frame,
            frame_id=1,
            reader=mock_reader,
            save_annotated=True,
            output_path=out_path,
        )

        assert annotated.shape == frame.shape
        assert len(results) == 1
        assert results[0]["frame_id"] == 1
        assert results[0]["plate_text"] == "GJ01AB1234"
        assert out_path.exists()
        saved_img = cv2.imread(str(out_path))
        assert saved_img is not None
        assert saved_img.shape == (480, 640, 3)


def test_save_artifacts_flag_toggle():
    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = Path(tmpdir) / "annotated_subdir" / "frame_toggle.jpg"
        frame = make_test_crop(w=640, h=480, seed=1)

        mock_reader = MagicMock()
        mock_reader.process_frame.return_value = [
            {"vehicle_bbox": [0, 0, 100, 100], "class": "car", "plate_detected": False}
        ]

        original_save = anpr.SAVE_ARTIFACTS
        try:
            # When SAVE_ARTIFACTS is False, image is NOT written to disk, but results are returned
            anpr.SAVE_ARTIFACTS = False
            annotated, results = anpr.process_anpr_frame(
                frame, frame_id=2, reader=mock_reader, save_annotated=True, output_path=out_path
            )
            assert not out_path.exists()
            assert len(results) == 1
            assert results[0]["frame_id"] == 2

            # When SAVE_ARTIFACTS is True, image is written
            anpr.SAVE_ARTIFACTS = True
            annotated, results = anpr.process_anpr_frame(
                frame, frame_id=3, reader=mock_reader, save_annotated=True, output_path=out_path
            )
            assert out_path.exists()
            assert len(results) == 1
        finally:
            anpr.SAVE_ARTIFACTS = original_save


@pytest.mark.asyncio
async def test_plate_worker_process_with_valid_reads():
    worker = PlateWorker()
    worker.reader = MagicMock()
    worker.reader.return_value = [
        anpr.PlateRead("GJ01AB1234", 0.95, 1, True),
        anpr.PlateRead("GJ01DB1234", 0.75, 2, True),
    ]

    mock_conn = AsyncMock()
    record = {
        "id": 1,
        "pipeline": "plate",
        "read_id": uuid.uuid4(),
        "camera_id": "cam-01",
        "crop_ref": "crops/cam-01/2026-09-13/12/read1.jpg",
        "payload": {"class": "car"},
        "attempts": 0,
    }
    job = queue.Job(record)
    crop = make_test_crop()

    result = await worker.process(mock_conn, job, crop)
    assert result == {"best": "GJ01AB1234", "conf": 0.95, "hypotheses": 2}

    mock_conn.execute.assert_any_call(
        "UPDATE sightings SET plate_text = $2, plate_conf = $3 WHERE read_id = $1",
        job.read_id,
        "GJ01AB1234",
        0.95,
    )
    mock_conn.execute.assert_any_call(
        "DELETE FROM plate_hypotheses WHERE read_id = $1", job.read_id
    )


@pytest.mark.asyncio
async def test_plate_worker_process_with_no_reads():
    worker = PlateWorker()
    worker.reader = MagicMock()
    worker.reader.return_value = []

    mock_conn = AsyncMock()
    record = {
        "id": 2,
        "pipeline": "plate",
        "read_id": uuid.uuid4(),
        "camera_id": "cam-01",
        "crop_ref": "crops/cam-01/2026-09-13/12/read2.jpg",
        "payload": {"class": "car"},
        "attempts": 0,
    }
    job = queue.Job(record)
    crop = make_test_crop()

    result = await worker.process(mock_conn, job, crop)
    assert result == {"reads": 0}
