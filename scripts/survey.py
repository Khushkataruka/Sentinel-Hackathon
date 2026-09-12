#!/usr/bin/env python3
"""Record provisional camera profiles.

A synced camera is already granted every capability by the registry's
defaults, so this no longer exists to unblock the pipelines. It remains the
way to stamp a row as provisional and to record a measured loop period.

It is not the section 6 survey. The four capability fields --
permitted_attributes, permitted_violations, plate_viable, density_viable --
are omitted from the request on purpose, so the registry applies the same
full grant every other write path gets and this script holds no second copy
of the lists. trust_level inherits whatever the sync recorded from coordinate
quality.

Use --loop-period to record what `sentinel-ingest preflight` measured.

    python scripts/survey.py --list
    python scripts/survey.py --camera 13 --loop-period 20.0
    python scripts/survey.py --all
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_REGISTRY = "http://localhost:8000"


MISSING = object()


def call(registry: str, method: str, path: str, user: str, body=None, allow_404=False):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"{registry.rstrip('/')}{path}", data=data, method=method,
        headers={"Content-Type": "application/json", "X-Sentinel-User": user},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read() or "null")
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and allow_404:
            return MISSING
        sys.exit(f"{method} {path} -> {exc.code} {exc.read().decode()[:300]}")
    except urllib.error.URLError as exc:
        sys.exit(f"cannot reach registry at {registry}: {exc.reason}")


def profile_of(registry: str, camera_id: str, user: str) -> dict | None:
    """The camera's profile, or None if it has no row at all."""
    result = call(registry, "GET", f"/cameras/{camera_id}/profile", user, allow_404=True)
    return None if result is MISSING else result


def backlog(registry: str, user: str) -> list[tuple[str, str, dict | None]]:
    """Cameras that will produce nothing useful yet.

    Not /cameras/needing-survey: that finds cameras with no profile row, and
    the sync writes a permit-nothing row for every camera it imports, so it
    reads empty while the whole estate is still unsurveyed.
    """
    out = []
    for cam in call(registry, "GET", "/cameras", user) or []:
        camera_id = cam["camera_id"]
        profile = profile_of(registry, camera_id, user)
        if not profile or not profile.get("permitted_attributes"):
            out.append((camera_id, cam.get("name", ""), profile))
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--registry", default=DEFAULT_REGISTRY)
    p.add_argument("--user", default="admin")
    p.add_argument("--camera", help="one camera id")
    p.add_argument("--all", action="store_true", help="every camera in the backlog")
    p.add_argument("--list", action="store_true", help="show what is waiting")
    p.add_argument("--loop-period", type=float, default=None,
                   help="seconds of PTS between loop points, from preflight")
    args = p.parse_args()

    if args.list:
        waiting = backlog(args.registry, args.user)
        for camera_id, name, profile in waiting:
            state = "no profile" if profile is None else "permits nothing"
            print(f"{camera_id:>6}  {name:<32} {state}")
        print(f"\n{len(waiting)} camera(s) waiting")
        return 0

    if args.camera:
        targets = [args.camera]
    elif args.all:
        targets = [c for c, _, _ in backlog(args.registry, args.user)]
    else:
        p.error("pass --camera, --all or --list")

    if not targets:
        print("nothing to do: every camera already permits something")
        return 0

    for camera_id in targets:
        existing = profile_of(args.registry, camera_id, args.user)
        resolution = (existing or {}).get("resolution_class", "thumbnail")

        profile = {
            # permitted_attributes, permitted_violations, plate_viable and
            # density_viable are omitted: the registry's defaults grant them
            # all, and repeating the lists here would give them two homes.
            "resolution_class": resolution,
            "decode_fps": (existing or {}).get("decode_fps"),
            "trust_level": (existing or {}).get("trust_level", 0.5),
            "loop_period_s": args.loop_period or (existing or {}).get("loop_period_s"),
            "distortion": {"survey": "provisional; not a section 6 survey"},
        }
        result = call(args.registry, "PUT", f"/cameras/{camera_id}/profile",
                      args.user, profile)
        print(f"{camera_id:>6}  {result['resolution_class']:<9} "
              f"attrs={','.join(result['permitted_attributes']) or '-':<18} "
              f"trust={result['trust_level']:<5} loop={result.get('loop_period_s')}")

    print(f"\n{len(targets)} provisional profile(s) written. "
          "Violations and plates are on: the grant is the default, not a survey.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
