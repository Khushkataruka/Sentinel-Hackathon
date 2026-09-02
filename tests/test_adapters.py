"""Adapter discovery. The promise is that a bad adapter is a row, not a crash."""

from __future__ import annotations

import textwrap
from pathlib import Path

from sentinel.core.types import AdapterStatus
from sentinel.ingest.adapters.loader import discover, load_one


def write_adapter(root: Path, name: str, toml: str, driver: str | None = None) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "adapter.toml").write_text(textwrap.dedent(toml))
    if driver is not None:
        (folder / "driver.py").write_text(textwrap.dedent(driver))
    return folder


GOOD_DRIVER = '''
    from sentinel.ingest.adapters.base import (
        BaseAdapter, CameraRef, HealthReport, StreamHandle,
    )

    class Good(BaseAdapter):
        name = "good"
        def enumerate(self):
            return [CameraRef("C1", "One", "rtsp://example/1", 20.0, 72.0)]
        def open(self, camera_id):
            return StreamHandle(camera_id, "rtsp://example/1", "tcp")
        def health(self, camera_id):
            return HealthReport(reachable=True)

    def build(config):
        return Good(config)
'''


def test_a_working_adapter_registers(tmp_path):
    folder = write_adapter(
        tmp_path, "good", 'name = "good"\ndriver = "./driver.py"\n', GOOD_DRIVER
    )
    result = load_one(folder)
    assert result.status is AdapterStatus.REGISTERED
    assert result.camera_count == 1
    assert result.last_error is None


def test_a_missing_manifest_fails_without_raising(tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()
    result = load_one(folder)
    assert result.status is AdapterStatus.FAILED
    assert "adapter.toml" in result.last_error


def test_a_driver_that_raises_on_import_fails_without_raising(tmp_path):
    folder = write_adapter(
        tmp_path, "broken", 'name = "broken"\ndriver = "./driver.py"\n',
        "raise RuntimeError('this adapter was written at 3am')\n",
    )
    result = load_one(folder)
    assert result.status is AdapterStatus.FAILED
    assert "3am" in result.last_error


def test_a_driver_with_no_build_function_fails(tmp_path):
    folder = write_adapter(
        tmp_path, "nobuild", 'name = "nobuild"\ndriver = "./driver.py"\n',
        "x = 1\n",
    )
    result = load_one(folder)
    assert result.status is AdapterStatus.FAILED
    assert "build" in result.last_error


def test_an_adapter_claiming_no_cameras_fails_the_self_test(tmp_path):
    folder = write_adapter(
        tmp_path, "nocams", 'name = "nocams"\ndriver = "./driver.py"\n',
        '''
        from sentinel.ingest.adapters.base import BaseAdapter, HealthReport

        class Empty(BaseAdapter):
            def enumerate(self): return []
            def open(self, camera_id): raise AssertionError("unreachable")
            def health(self, camera_id): return HealthReport(reachable=True)

        def build(config): return Empty(config)
        ''',
    )
    assert load_one(folder).status is AdapterStatus.FAILED


def test_an_rtsp_adapter_that_does_not_force_tcp_is_rejected(tmp_path):
    """UDP fails across NAT and most corporate firewalls, and partial
    delivery produces corrupt frames that look like model bugs."""
    folder = write_adapter(
        tmp_path, "udp", 'name = "udp"\ndriver = "./driver.py"\n',
        '''
        from sentinel.ingest.adapters.base import (
            BaseAdapter, CameraRef, HealthReport, StreamHandle,
        )

        class Udp(BaseAdapter):
            def enumerate(self):
                return [CameraRef("C1", "One", "rtsp://example/1")]
            def open(self, camera_id):
                return StreamHandle(camera_id, "rtsp://example/1", "udp")
            def health(self, camera_id):
                return HealthReport(reachable=True)

        def build(config): return Udp(config)
        ''',
    )
    result = load_one(folder)
    assert result.status is AdapterStatus.FAILED
    assert "TCP" in result.last_error


def test_one_broken_adapter_does_not_stop_the_others(tmp_path):
    write_adapter(tmp_path, "good", 'name = "good"\ndriver = "./driver.py"\n', GOOD_DRIVER)
    write_adapter(tmp_path, "bad", 'name = "bad"\ndriver = "./driver.py"\n',
                  "raise ValueError('nope')\n")
    results = discover(tmp_path)
    statuses = {r.name: r.status for r in results}
    assert statuses["good"] is AdapterStatus.REGISTERED
    assert statuses["bad"] is AdapterStatus.FAILED


def test_the_shipped_adapter_configs_parse(tmp_path):
    """The example configs in adapters/ must at least be valid TOML with a
    driver, or the first thing anyone does is hit a parse error."""
    import tomllib

    root = Path(__file__).resolve().parent.parent / "adapters"
    for manifest in root.glob("*/adapter.toml"):
        config = tomllib.loads(manifest.read_text())
        assert config.get("name"), manifest
        assert config.get("driver"), manifest
