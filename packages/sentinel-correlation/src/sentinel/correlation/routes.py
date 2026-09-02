"""Route building.

What we output is a list of possible routes, not one route. If forty routes
are physically consistent, we show forty, ranked, with the count visible.
Picking one and presenting it as the answer would be inventing certainty the
evidence does not contain.

The speed check throws out impossible routes, not wrong ones, and it is
important to be clear about the difference. Two different white Swifts on the
same road twenty minutes apart produce a link that passes every physical
check and is completely wrong. The check has nothing to say about that. What
answers it is the competing-route count, the rarity weighting, and a plate
anchor where one exists.

Rejected legs are kept, with the reason. "Why was this link dropped" is an
audit answer, and throwing the evidence away makes it unanswerable.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import asyncpg
from sentinel.core.config import settings
from sentinel.core.logging import get_logger
from sentinel.core.models import Candidate, RouteLeg

log = get_logger(__name__)


@dataclass
class CameraGeometry:
    camera_id: str
    lat: float
    lon: float
    bearing_deg: float | None = None
    range_m: float | None = None
    corridor_group: str | None = None
    trust_level: float = 0.5


async def load_geometry(
    conn: asyncpg.Connection, camera_ids: list[str]
) -> dict[str, CameraGeometry]:
    rows = await conn.fetch(
        """
        SELECT c.camera_id,
               ST_Y(c.location::geometry) AS lat,
               ST_X(c.location::geometry) AS lon,
               c.bearing_deg, c.range_m,
               p.corridor_group, COALESCE(p.trust_level, 0.5) AS trust_level
          FROM cameras c
          LEFT JOIN camera_profiles p USING (camera_id)
         WHERE c.camera_id = ANY($1)
        """,
        camera_ids,
    )
    return {r["camera_id"]: CameraGeometry(**dict(r)) for r in rows}


async def great_circle_km(
    conn: asyncpg.Connection, a: CameraGeometry, b: CameraGeometry
) -> float:
    """Straight-line distance between two cameras.

    PostGIS does this properly on the spheroid. It is a lower bound on road
    distance, which makes the speed check conservative: we only reject a leg
    when it is impossible even travelling in a straight line. Rejecting on a
    road-network distance would be tighter and would need a road graph we do
    not have for the whole state.
    """
    return float(
        await conn.fetchval(
            "SELECT ST_Distance(ST_MakePoint($1,$2)::geography,"
            " ST_MakePoint($3,$4)::geography) / 1000.0",
            a.lon, a.lat, b.lon, b.lat,
        )
    )


@dataclass
class Leg:
    seq: int
    from_read_id: uuid.UUID
    to_read_id: uuid.UUID
    from_camera: str
    to_camera: str
    distance_km: float
    elapsed_s: float
    required_speed_kmh: float
    plausible: bool
    drop_reason: str | None = None
    gap_s: float | None = None

    def to_model(self) -> RouteLeg:
        return RouteLeg(
            seq=self.seq, from_read_id=self.from_read_id, to_read_id=self.to_read_id,
            distance_km=self.distance_km, elapsed_s=self.elapsed_s,
            required_speed_kmh=self.required_speed_kmh, plausible=self.plausible,
            drop_reason=self.drop_reason, gap_s=self.gap_s,
        )


@dataclass
class RouteCandidate:
    sightings: list[Candidate]
    legs: list[Leg] = field(default_factory=list)
    #: True when enumeration hit the cap, so competing_count is a lower
    #: bound rather than a total. Surfaced to the operator, not hidden.
    count_capped: bool = False

    @property
    def plate_anchored(self) -> bool:
        """Any leg backed by an OCR read.

        A route anchored on even one plate read is worth far more than one
        built entirely from descriptions, because that record contains an
        actual identity rather than a category.
        """
        return any("plate" in s.matched_on for s in self.sightings)

    @property
    def min_trust(self) -> float:
        return min((s.trust_level for s in self.sightings), default=0.0)

    @property
    def span_s(self) -> float:
        if len(self.sightings) < 2:
            return 0.0
        return (self.sightings[-1].seen_at - self.sightings[0].seen_at).total_seconds()


async def evaluate_leg(
    conn: asyncpg.Connection,
    geometry: dict[str, CameraGeometry],
    seq: int,
    a: Candidate,
    b: Candidate,
) -> Leg:
    """Check one hop for physical possibility."""
    ga, gb = geometry.get(a.camera_id), geometry.get(b.camera_id)
    elapsed = (b.seen_at - a.seen_at).total_seconds()

    if ga is None or gb is None:
        return Leg(seq, a.read_id, b.read_id, a.camera_id, b.camera_id,
                   0.0, elapsed, 0.0, False, "camera geometry unknown")

    distance = await great_circle_km(conn, ga, gb)

    if elapsed <= 0:
        return Leg(seq, a.read_id, b.read_id, a.camera_id, b.camera_id,
                   distance, elapsed, 0.0, False, "sightings not in time order")

    speed = distance / (elapsed / 3600.0)

    if speed > settings.max_plausible_speed_kmh:
        reason = f"implies {speed:.0f} km/h over {distance:.1f} km"
        plausible = False
    elif distance > 0.3 and speed < settings.min_plausible_speed_kmh:
        # Very slow over a real distance is not impossible -- it is a vehicle
        # that stopped. Kept, but flagged, because it is also what two
        # different vehicles look like.
        reason = f"implies {speed:.1f} km/h; vehicle stopped, or two vehicles"
        plausible = True
    else:
        reason = None
        plausible = True

    # An uncovered stretch. Where no camera watches the road between two
    # sightings, the output has to say so rather than imply we watched it.
    gap_s = None
    if plausible and ga.corridor_group and gb.corridor_group:
        if ga.corridor_group != gb.corridor_group:
            gap_s = elapsed
    elif plausible and distance > 2.0:
        gap_s = elapsed

    return Leg(seq, a.read_id, b.read_id, a.camera_id, b.camera_id,
               distance, elapsed, speed, plausible, reason, gap_s)


async def enumerate_routes(
    conn: asyncpg.Connection,
    candidates: list[Candidate],
    *,
    max_legs: int | None = None,
    max_routes: int | None = None,
) -> tuple[list[RouteCandidate], list[Leg]]:
    """Every physically consistent chain through the candidates.

    Not one greedy path. Depth-first over time-ordered sightings, pruned by
    the speed check at each hop.

    Returns (routes, rejected_legs). The rejected legs are kept so the audit
    can answer why a link was dropped.

    More cameras makes this worse, not better: more sightings of common cars
    means more physically-possible routes. That is a property of the problem,
    not of this function, and the competing-route count is how we report it
    honestly instead of hiding it.
    """
    max_legs = max_legs or settings.max_route_legs
    max_routes = max_routes or settings.max_routes

    ordered = sorted(candidates, key=lambda c: c.seen_at)
    if len(ordered) < 2:
        return ([RouteCandidate(sightings=ordered)] if ordered else []), []

    geometry = await load_geometry(conn, sorted({c.camera_id for c in ordered}))

    # Precompute the hop matrix once. O(n^2) evaluations rather than one per
    # path prefix, which is what makes the enumeration affordable.
    hops: dict[tuple[int, int], Leg] = {}
    rejected: list[Leg] = []
    for i in range(len(ordered)):
        for j in range(i + 1, len(ordered)):
            leg = await evaluate_leg(conn, geometry, 0, ordered[i], ordered[j])
            if leg.plausible:
                hops[(i, j)] = leg
            else:
                rejected.append(leg)

    routes: list[RouteCandidate] = []

    def walk(path: list[int]) -> None:
        if len(routes) >= max_routes:
            return
        last = path[-1]
        extended = False
        if len(path) < max_legs + 1:
            for j in range(last + 1, len(ordered)):
                if (last, j) in hops:
                    extended = True
                    walk([*path, j])
                    if len(routes) >= max_routes:
                        return
        # A path that cannot be extended is a complete route. Single sightings
        # are routes too -- a vehicle seen once has been somewhere.
        if not extended:
            sightings = [ordered[i] for i in path]
            legs = [
                Leg(
                    seq=k + 1, **{
                        f: getattr(hops[(path[k], path[k + 1])], f)
                        for f in (
                            "from_read_id", "to_read_id", "from_camera", "to_camera",
                            "distance_km", "elapsed_s", "required_speed_kmh",
                            "plausible", "drop_reason", "gap_s",
                        )
                    }
                )
                for k in range(len(path) - 1)
            ]
            routes.append(RouteCandidate(sightings=sightings, legs=legs))

    capped = False
    for start in range(len(ordered)):
        walk([start])
        if len(routes) >= max_routes:
            capped = True
            # The competing-route count is evidence an operator reads. A
            # capped count understates uncertainty, so it is marked rather
            # than quietly returned as if it were complete.
            log.warning("route_enumeration_capped", cap=max_routes,
                        candidates=len(ordered),
                        consequence="competing_count is a floor, not a total")
            break

    # Drop routes that are a strict prefix of a longer one: they carry no
    # information the longer route does not.
    signatures = {tuple(s.read_id for s in r.sightings) for r in routes}
    routes = [
        r for r in routes
        if not any(
            sig != tuple(s.read_id for s in r.sightings)
            and sig[: len(r.sightings)] == tuple(s.read_id for s in r.sightings)
            for sig in signatures
        )
    ]

    for route in routes:
        route.count_capped = capped

    return routes, rejected


async def persist(
    conn: asyncpg.Connection,
    search_id: uuid.UUID,
    scored: list[tuple[RouteCandidate, float, int | None]],
    rejected: list[Leg],
) -> list[uuid.UUID]:
    """Write routes and every leg, including the rejected ones.

    competing_count is stored on each route, not computed at read time, so
    the number an operator saw is the number the audit log can reproduce.
    """
    competing = len(scored)
    route_ids: list[uuid.UUID] = []

    for rank, (route, score, rarity_count) in enumerate(scored, start=1):
        route_id = await conn.fetchval(
            """
            INSERT INTO routes (search_id, rank, score, competing_count,
                                rarity_count, plate_anchored, min_trust)
            VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id
            """,
            search_id, rank, score, competing, rarity_count,
            route.plate_anchored, route.min_trust,
        )
        route_ids.append(route_id)

        for leg in route.legs:
            await conn.execute(
                """
                INSERT INTO route_legs (route_id, seq, from_read_id, to_read_id,
                    distance_km, elapsed_s, required_speed_kmh, plausible,
                    drop_reason, gap_s)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                """,
                route_id, leg.seq, leg.from_read_id, leg.to_read_id,
                leg.distance_km, leg.elapsed_s, leg.required_speed_kmh,
                leg.plausible, leg.drop_reason, leg.gap_s,
            )

    # Rejected legs hang off the top-ranked route, so the audit trail for a
    # search includes what it refused as well as what it returned.
    if route_ids and rejected:
        for offset, leg in enumerate(rejected, start=1):
            await conn.execute(
                """
                INSERT INTO route_legs (route_id, seq, from_read_id, to_read_id,
                    distance_km, elapsed_s, required_speed_kmh, plausible, drop_reason)
                VALUES ($1,$2,$3,$4,$5,$6,$7,false,$8)
                """,
                route_ids[0], -offset, leg.from_read_id, leg.to_read_id,
                leg.distance_km, leg.elapsed_s, leg.required_speed_kmh,
                leg.drop_reason,
            )

    return route_ids
