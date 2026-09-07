"""The PTS clock is where the sandbox guide's timing rules actually live."""

from __future__ import annotations

from datetime import UTC, datetime

from sentinel.ingest.ptsclock import PtsClock


def test_first_frame_anchors():
    clock = PtsClock()
    when, dt, cut = clock.observe(100.0)
    assert clock.anchored
    assert dt == 0.0 and cut is False
    assert isinstance(when, datetime)


def test_wall_time_follows_pts_not_arrival():
    """Timestamps come from PTS deltas, so a burst of frames arriving in
    milliseconds still spans the right amount of time."""
    clock = PtsClock()
    anchor = datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC)
    clock.anchor(0.0, anchor)

    when, dt, _ = clock.observe(2.5)
    assert (when - anchor).total_seconds() == 2.5
    assert dt == 2.5


def test_gop_replay_does_not_produce_impossible_deltas():
    """On connect the gateway replays a buffered GOP faster than real time.
    Timing by arrival would compute impossible velocities; timing by PTS
    gives the real intervals whatever speed they showed up at."""
    clock = PtsClock()
    clock.observe(0.0)
    deltas = [clock.observe(pts)[1] for pts in (0.1, 0.2, 0.3, 0.4)]
    assert all(abs(d - 0.1) < 1e-9 for d in deltas)


def test_backwards_pts_is_a_discontinuity():
    """The feed loops. A backwards jump is the loop point, not noise."""
    clock = PtsClock()
    clock.observe(600.0)
    _, dt, cut = clock.observe(0.5)
    assert cut is True
    assert dt == 0.0
    assert clock.discontinuities == 1


def test_large_forward_jump_is_a_discontinuity():
    clock = PtsClock(discontinuity_s=5.0)
    clock.observe(10.0)
    _, _, cut = clock.observe(90.0)
    assert cut is True


def test_ordinary_gap_is_not_a_discontinuity():
    """Frame intervals are not uniform. A gap must not read as a disconnect."""
    clock = PtsClock(discontinuity_s=5.0)
    clock.observe(10.0)
    _, dt, cut = clock.observe(12.5)
    assert cut is False
    assert dt == 2.5


def test_reanchors_after_a_cut():
    """A cut re-pins the clock to the present, so timestamps after it are not
    offset from the pre-cut anchor.

    Asserted as "close to now", not "later than the old anchor": the old
    anchor here is a fabricated instant and the re-anchor uses the real
    clock, so ordering between them proves nothing.
    """
    clock = PtsClock()
    clock.anchor(0.0, datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC))
    clock.observe(2.0)

    before = datetime.now(UTC)
    when, dt, cut = clock.observe(600.0)     # a large forward jump: the cut
    after = datetime.now(UTC)

    assert cut is True
    assert dt == 0.0
    assert before <= when <= after

    # And the frame after the cut is measured from the NEW anchor.
    following, dt_after, cut_after = clock.observe(600.5)
    assert cut_after is False
    assert dt_after == 0.5
    assert abs((following - when).total_seconds() - 0.5) < 1e-6


def test_measured_fps_ignores_the_declared_rate():
    clock = PtsClock()
    for i in range(11):
        clock.observe(i * 0.1)
    assert abs(clock.measured_fps() - 10.0) < 0.01


def test_fixed_epoch_pins_a_file_to_a_known_instant():
    """A file has no wall clock of its own.

    Without this, two videos ingested back to back land however many seconds
    apart the first one took to process, and every cross-camera route leg
    computes a speed from that accident.
    """
    epoch = datetime(2026, 9, 7, 9, 0, 0, tzinfo=UTC)
    clock = PtsClock(fixed_epoch=epoch)

    first, _, _ = clock.observe(0.0)
    assert first == epoch

    later, _, _ = clock.observe(3.25)
    assert (later - epoch).total_seconds() == 3.25


def test_fixed_epoch_survives_a_loop_cut():
    """Re-anchoring must stay on the epoch, not jump to now().

    A looping file cuts mid-pass; if the cut re-anchored to wall clock the
    second half of the video would be timestamped hours from the first.
    """
    epoch = datetime(2026, 9, 7, 9, 0, 0, tzinfo=UTC)
    clock = PtsClock(fixed_epoch=epoch)

    clock.observe(0.0)
    clock.observe(19.9)
    after_cut, _, cut = clock.observe(0.1)      # backwards: the loop point

    assert cut is True
    assert (after_cut - epoch).total_seconds() == 0.1
