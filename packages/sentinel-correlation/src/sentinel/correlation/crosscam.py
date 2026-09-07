"""Correlating a batch of videos against each other.

The platform's search starts from an operator: a registration number, or a
description someone typed. A batch of video files has no operator and no
question -- the question is "did anything here appear twice".

So this seeds a search from each sighting instead. A sighting already carries
everything `search.run` wants: attributes from the describe pipeline, an
appearance embedding from embed, a plate from the ANPR pipeline. Turning one
into a `VehicleDescription` and handing it to the existing search is the whole
trick. No new matching, no new scoring, no second definition of what a route
is -- the candidate SQL, the speed plausibility checks, the rarity weighting
and the competing count are the ones the rest of the platform uses, and a
number reported here means the same thing it means on the map.

What is new is the framing of the output: a match is a route that visits more
than one camera, deduplicated, numbered, and pointed back at the crops so a
person can check it. A route within a single camera is that camera seeing the
same vehicle twice; interesting, but not a correlation.
"""

from __future__ import annotations

import asyncio
import base64
import html
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
from sentinel.core import media
from sentinel.core.logging import get_logger
from sentinel.core.models import VehicleDescription
from sentinel.core.types import SearchKind
from sentinel.correlation import search as search_mod

log = get_logger(__name__)

#: How many sightings to seed a search from. Every seed is a full search --
#: candidate scan, route enumeration, scoring, persist -- so this is the knob
#: that decides whether a run takes a minute or an hour. The ordering below
#: means the cap falls on the least informative sightings first.
DEFAULT_MAX_SEEDS = 50

#: Widen the search window past the sightings themselves, so a route can be
#: built from a vehicle that entered one video before the other started.
WINDOW_PAD = timedelta(minutes=30)


async def seeds(
    conn: asyncpg.Connection, camera_ids: list[str], limit: int
) -> list[asyncpg.Record]:
    """Sightings worth searching from, best evidence first.

    A plate is the strongest anchor there is, an embedding is next, and a bare
    colour-and-type is a description that half the road matches. When the cap
    bites it should bite on the last of those.
    """
    return await conn.fetch(
        """
        SELECT read_id, camera_id, seen_at, class, colour, vtype, make, model,
               caption, plate_text, plate_conf, crop_ref,
               embedding::text AS embedding_text
          FROM sightings
         WHERE camera_id = ANY($1::text[])
           AND (plate_text IS NOT NULL OR embedding IS NOT NULL OR colour IS NOT NULL)
         ORDER BY (plate_text IS NOT NULL) DESC,
                  (embedding IS NOT NULL) DESC,
                  coalesce(plate_conf, 0) DESC,
                  seen_at
         LIMIT $2
        """,
        camera_ids, limit,
    )


def _description(row: asyncpg.Record) -> VehicleDescription:
    """A sighting, restated as the thing the platform knows how to search for."""
    embedding = None
    if row["embedding_text"]:
        # pgvector renders as '[1,2,3]'. Round-tripping through text rather
        # than a vector codec keeps this file free of an extension dependency.
        embedding = [float(v) for v in row["embedding_text"].strip("[]").split(",") if v]
    return VehicleDescription(
        registration_no=row["plate_text"],
        colour=row["colour"],
        vtype=row["vtype"] or row["class"],
        make=row["make"],
        model=row["model"],
        embedding=embedding,
    )


async def window(
    conn: asyncpg.Connection, camera_ids: list[str]
) -> tuple[datetime | None, datetime | None]:
    row = await conn.fetchrow(
        "SELECT min(seen_at) AS lo, max(seen_at) AS hi FROM sightings "
        "WHERE camera_id = ANY($1::text[])",
        camera_ids,
    )
    if row is None or row["lo"] is None:
        return None, None
    return row["lo"] - WINDOW_PAD, row["hi"] + WINDOW_PAD


async def _sighting_details(
    conn: asyncpg.Connection, read_ids: list[uuid.UUID]
) -> dict[str, dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT s.read_id, s.camera_id, c.name AS camera_name, s.seen_at, s.class,
               s.colour, s.vtype, s.make, s.model, s.caption,
               s.plate_text, s.plate_conf, s.crop_ref,
               coalesce(
                 array_agg(v.violation_type ORDER BY v.violation_type)
                   FILTER (WHERE v.violation_type IS NOT NULL), '{}'
               ) AS violations
          FROM sightings s
          JOIN cameras c ON c.camera_id = s.camera_id
          LEFT JOIN violations v ON v.read_id = s.read_id
         WHERE s.read_id = ANY($1::uuid[])
         GROUP BY s.read_id, c.name
        """,
        read_ids,
    )
    return {str(r["read_id"]): dict(r) for r in rows}


async def _legs(conn: asyncpg.Connection, route_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT seq, from_read_id, to_read_id, distance_km, elapsed_s,
               required_speed_kmh, plausible, drop_reason, gap_s
          FROM route_legs WHERE route_id = $1 AND seq >= 0 ORDER BY seq
        """,
        route_id,
    )
    return [
        {
            "seq": r["seq"],
            "from_read_id": str(r["from_read_id"]),
            "to_read_id": str(r["to_read_id"]),
            "distance_km": round(float(r["distance_km"]), 3),
            "elapsed_s": round(float(r["elapsed_s"]), 1),
            "required_speed_kmh": round(float(r["required_speed_kmh"]), 1),
            "plausible": r["plausible"],
            "drop_reason": r["drop_reason"],
            "gap_s": r["gap_s"],
        }
        for r in rows
    ]


def dedupe(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cross-camera routes only, one per distinct set of sightings, best first.

    Two things happen here and both matter.

    A route confined to one camera is that camera seeing the same vehicle
    twice. Real, and not a correlation -- the question a batch of videos asks
    is whether anything crossed between them.

    And every seed searches independently, so a vehicle seen in three videos
    is discovered from each end and scores slightly differently each time.
    Keyed on the set of sightings it visits, that is one match, not three;
    the highest-scoring discovery of it is the one worth keeping.
    """
    best: dict[frozenset[str], dict[str, Any]] = {}
    for route in routes:
        if len(set(route["cameras"])) < 2:
            continue
        key = frozenset(str(r) for r in route["read_ids"])
        previous = best.get(key)
        if previous is None or route["score"] > previous["score"]:
            best[key] = route
    return sorted(best.values(), key=lambda r: -r["score"])


async def correlate(
    conn: asyncpg.Connection, camera_ids: list[str], *, max_seeds: int = DEFAULT_MAX_SEEDS,
) -> dict[str, Any]:
    """Search from every promising sighting; keep the cross-camera routes."""
    since, until = await window(conn, camera_ids)
    if since is None:
        log.warning("no_sightings_to_correlate", cameras=camera_ids)
        return {"cameras": camera_ids, "seeds": 0, "matches": []}

    rows = await seeds(conn, camera_ids, max_seeds)
    log.info("crosscam_start", cameras=len(camera_ids), seeds=len(rows),
             since=since.isoformat(), until=until.isoformat())

    found: list[dict[str, Any]] = []
    for row in rows:
        try:
            # A savepoint per seed. Without one, a single failed search aborts
            # the enclosing transaction and every seed after it fails too --
            # the except below would catch each one and the run would report
            # zero matches with no obvious cause.
            async with conn.transaction():
                result = await search_mod.run(
                    conn, _description(row), kind=SearchKind.DESCRIPTION,
                    since=since, until=until, camera_ids=camera_ids,
                )
        except Exception as exc:
            log.error("seed_search_failed", read_id=str(row["read_id"]),
                      error=f"{type(exc).__name__}: {exc}")
            continue
        found.extend(
            {
                **route,
                "seed_read_id": str(row["read_id"]),
                "search_id": str(result.search_id),
                "rarity_count": result.rarity_count,
            }
            for route in result.routes
        )

    ordered = dedupe(found)
    detail = await _sighting_details(
        conn, [uuid.UUID(rid) for route in ordered for rid in route["read_ids"]]
    )

    matches: list[dict[str, Any]] = []
    for index, route in enumerate(ordered, start=1):
        matches.append({
            "match_id": f"MATCH-{index}",
            "score": round(float(route["score"]), 4),
            "competing_count": route["competing_count"],
            "competing_count_capped": route["competing_count_capped"],
            "plate_anchored": route["plate_anchored"],
            "min_trust": route["min_trust"],
            "rarity_count": route["rarity_count"],
            "cameras": route["cameras"],
            "route_id": route["route_id"],
            "search_id": route["search_id"],
            "seed_read_id": route["seed_read_id"],
            "explain": route["explain"],
            "legs": await _legs(conn, uuid.UUID(route["route_id"])),
            "sightings": [
                _public_sighting(detail.get(rid, {"read_id": rid}))
                for rid in route["read_ids"]
            ],
        })

    log.info("crosscam_done", seeds=len(rows), matches=len(matches))
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "cameras": camera_ids,
        "window": {"since": since.isoformat(), "until": until.isoformat()},
        "seeds": len(rows),
        "max_seeds": max_seeds,
        "matches": matches,
    }


def _public_sighting(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "read_id": str(row.get("read_id")),
        "camera_id": row.get("camera_id"),
        "camera_name": row.get("camera_name"),
        "seen_at": row["seen_at"].isoformat() if row.get("seen_at") else None,
        "class": row.get("class"),
        "colour": row.get("colour"),
        "vtype": row.get("vtype"),
        "make": row.get("make"),
        "model": row.get("model"),
        "caption": row.get("caption"),
        "plate_text": row.get("plate_text"),
        "plate_conf": row.get("plate_conf"),
        "crop_ref": row.get("crop_ref"),
        "violations": list(row.get("violations") or []),
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _thumb(crop_ref: str | None) -> str | None:
    """Embed the crop, so the report is one file that survives being emailed."""
    if not crop_ref:
        return None
    path = media.absolute(crop_ref)
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")


def render_html(report: dict[str, Any]) -> str:
    e = html.escape
    matches = report.get("matches", [])

    cards = []
    for match in matches:
        crops = []
        for sighting in match["sightings"]:
            thumb = _thumb(sighting.get("crop_ref"))
            caption = " · ".join(
                p for p in (sighting.get("colour"), sighting.get("vtype") or sighting.get("class"),
                            sighting.get("plate_text")) if p
            ) or "—"
            picture = (
                f'<img src="{thumb}" alt="">' if thumb
                else '<span class="nocrop">no crop</span>'
            )
            cam = e(str(sighting.get("camera_id")))
            clock = e(str(sighting.get("seen_at") or "")[11:19])
            crops.append(
                f'<figure><div class="thumb">{picture}</div>'
                f'<figcaption><b>{cam}</b><br>{clock}<br>'
                f'{e(caption)}</figcaption></figure>'
            )

        legs = "".join(
            f"<tr><td>{leg['seq']}</td><td>{leg['distance_km']} km</td>"
            f"<td>{leg['elapsed_s']} s</td><td>{leg['required_speed_kmh']} km/h</td>"
            f"<td>{'yes' if leg['plausible'] else e(str(leg['drop_reason'] or 'no'))}</td>"
            f"<td>{'' if leg['gap_s'] is None else str(round(leg['gap_s'], 1)) + ' s'}</td></tr>"
            for leg in match["legs"]
        )

        badges = [f'score {match["score"]:.3f}', f'{match["competing_count"]} competing']
        if match["competing_count_capped"]:
            badges.append("count capped — a floor, not a total")
        if match["plate_anchored"]:
            badges.append("plate anchored")
        if match.get("rarity_count") is not None:
            badges.append(f'population {match["rarity_count"]}')
        if match.get("min_trust") is not None:
            badges.append(f'min trust {match["min_trust"]:.2f}')

        cards.append(f"""
    <section class="card">
      <h2>{e(match["match_id"])} <small>{e(" → ".join(match["cameras"]))}</small></h2>
      <p class="badges">{"".join(f'<span>{e(b)}</span>' for b in badges)}</p>
      <div class="crops">{"".join(crops)}</div>
      <table><thead><tr><th>leg</th><th>distance</th><th>elapsed</th>
        <th>required speed</th><th>plausible</th><th>uncovered</th></tr></thead>
        <tbody>{legs}</tbody></table>
    </section>""")

    empty = "" if matches else (
        '<p class="empty">No route visited more than one camera. Either nothing '
        'in these videos appeared twice, or the appearance model is a stub and '
        'cannot tell two vehicles apart. Check the run summary for which models '
        'were real.</p>'
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sentinel — cross-camera correlations</title>
<style>
  :root {{ color-scheme: light dark;
    --bg:#f6f6f4; --fg:#1a1a1a; --card:#fff; --line:#dfdfda; --dim:#6a6a66; }}
  @media (prefers-color-scheme: dark) {{ :root {{
    --bg:#151516; --fg:#ececea; --card:#1e1e20; --line:#33333a; --dim:#9a9a96; }} }}
  body {{ margin:0; padding:2rem 1.25rem; background:var(--bg); color:var(--fg);
    font:15px/1.5 ui-sans-serif,system-ui,-apple-system,sans-serif; }}
  main {{ max-width:60rem; margin:0 auto; }}
  h1 {{ font-size:1.4rem; margin:0 0 .25rem; }}
  .meta {{ color:var(--dim); margin:0 0 2rem; font-size:.9rem; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:10px;
    padding:1.1rem 1.25rem; margin-bottom:1.25rem; }}
  .card h2 {{ font-size:1.05rem; margin:0 0 .5rem; }}
  .card h2 small {{ color:var(--dim); font-weight:400; margin-left:.5rem; }}
  .badges span {{ display:inline-block; border:1px solid var(--line); border-radius:99px;
    padding:.12rem .6rem; margin:0 .35rem .35rem 0; font-size:.78rem; color:var(--dim); }}
  .crops {{ display:flex; gap:.75rem; flex-wrap:wrap; margin:.5rem 0 1rem; }}
  figure {{ margin:0; width:9.5rem; }}
  .thumb {{ height:7rem; display:flex; align-items:center; justify-content:center;
    background:var(--bg); border:1px solid var(--line); border-radius:6px; overflow:hidden; }}
  .thumb img {{ max-width:100%; max-height:100%; }}
  .nocrop {{ color:var(--dim); font-size:.75rem; }}
  figcaption {{ font-size:.75rem; color:var(--dim); margin-top:.35rem; line-height:1.35; }}
  table {{ width:100%; border-collapse:collapse; font-size:.82rem; }}
  th, td {{ text-align:left; padding:.3rem .5rem; border-bottom:1px solid var(--line); }}
  th {{ color:var(--dim); font-weight:500; }}
  .empty {{ color:var(--dim); }}
  footer {{ color:var(--dim); font-size:.8rem; margin-top:2rem; }}
</style></head><body><main>
  <h1>Cross-camera correlations</h1>
  <p class="meta">{e(str(report.get("generated_at", "")))} ·
    {len(matches)} match{"" if len(matches) == 1 else "es"} from
    {report.get("seeds", 0)} seed sightings across
    {e(", ".join(report.get("cameras", [])))}</p>
  {empty}{"".join(cards)}
  <footer>Every physically consistent route is listed with its competing count.
    A high competing count means the description fits many vehicles, not that
    the match is wrong — and a capped count is a floor, not a total.</footer>
</main></body></html>
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(
    camera_ids: list[str], out: str, *, max_seeds: int = DEFAULT_MAX_SEEDS,
    html_out: str | None = None,
) -> int:
    from sentinel.core.db import close_pool, transaction

    async def _go() -> dict[str, Any]:
        try:
            async with transaction() as conn:
                return await correlate(conn, camera_ids, max_seeds=max_seeds)
        finally:
            await close_pool()

    report = asyncio.run(_go())
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    if html_out:
        Path(html_out).parent.mkdir(parents=True, exist_ok=True)
        Path(html_out).write_text(render_html(report), encoding="utf-8")

    print(json.dumps(
        {"matches": len(report["matches"]), "seeds": report["seeds"],
         "json": str(out_path), "html": html_out},
        indent=2,
    ))
    return 0


__all__ = [
    "seeds", "dedupe", "correlate", "render_html", "main", "DEFAULT_MAX_SEEDS",
]
