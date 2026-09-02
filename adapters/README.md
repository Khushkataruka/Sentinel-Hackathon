# Adapter folder

Drop a folder in here with an `adapter.toml`. The platform finds it on start,
tests it, and either registers it or records the failure in the `adapters`
table with a readable `last_error`. A broken adapter never takes the process
down.

An `adapter.toml` needs two things:

    name   = "district-rtsp"
    driver = "sentinel.ingest.adapters.builtin.rtsp"

`driver` is either a dotted module path or `./driver.py` relative to the
folder. The module must expose `build(config) -> Adapter` and the object it
returns must implement `enumerate()`, `open(camera_id)` and `health(camera_id)`.
`seek(camera_id, ts)` is optional and nothing depends on it.

Check what loaded:

    uv run sentinel-ingest adapters
