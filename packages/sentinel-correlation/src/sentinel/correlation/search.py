"""Running a search, end to end.

Steps 1-8 of section 4.1:

    1  registration number          ->  VAHAN lookup
    2  VAHAN record                 ->  description, and how many vehicles match it
    3  description plus rarity      ->  candidate sightings in the window
    4  plate guesses                ->  fuzzy matches merged in
    5  candidate set                ->  ranked by appearance similarity
    6  ranked sightings             ->  every physically possible route
    7  routes                       ->  ranked, with the competing count
    8  chosen route                 ->  map, evidence, gaps, export

Step 2's count is not decoration. It is what turns "white Swift" into a
number instead of a shrug.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import asyncpg
from sentinel.core import audit
from sentinel.core.config import settings
from sentinel.core.logging import get_logger
from sentinel.core.models import Candidate, VehicleDescription
from sentinel.core.types import ActorKind, SearchKind
from sentinel.correlation import candidates as cand
from sentinel.correlation import routes as route_builder
from sentinel.correlation import scoring, vahan

log = get_logger(__name__)


@dataclass
class SearchResult:
    search_id: uuid.UUID
    description: VehicleDescription
    rarity_count: int | None
    candidates: list[Candidate] = field(default_factory=list)
    routes: list[dict[str, Any]] = field(default_factory=list)
    rejected_legs: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "search_id": str(self.search_id),
            "description": self.description.model_dump(exclude={"embedding"}),
            "rarity_count": self.rarity_count,
            "candidate_count": len(self.candidates),
            "route_count": len(self.routes),
            "rejected_leg_count": self.rejected_legs,
            "routes": self.routes,
        }


async def create_search(
    conn: asyncpg.Connection,
    kind: SearchKind,
    params: dict[str, Any],
    *,
    requested_by: uuid.UUID | None = None,
    watchlist_entry_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Every search gets a row, so routes have a parent and the audit log has
    an anchor."""
    search_id = await conn.fetchval(
        """
        INSERT INTO searches (kind, params, watchlist_entry_id, requested_by)
        VALUES ($1,$2,$3,$4) RETURNING id
        """,
        kind.value, params, watchlist_entry_id, requested_by,
    )
    await audit.write(
        conn,
        actor_kind=ActorKind.USER if requested_by else ActorKind.SERVICE,
        actor_id=str(requested_by) if requested_by else "correlation",
        action="search.create", object_type="search", object_id=str(search_id),
        details={"kind": kind.value, "params": params},
    )
    return search_id


async def run(
    conn: asyncpg.Connection,
    description: VehicleDescription,
    *,
    kind: SearchKind = SearchKind.DESCRIPTION,
    since: datetime | None = None,
    until: datetime | None = None,
    camera_ids: list[str] | None = None,
    requested_by: uuid.UUID | None = None,
    watchlist_entry_id: uuid.UUID | None = None,
    district: str | None = None,
) -> SearchResult:
    """Find candidates, build routes, score, persist."""
    params = description.model_dump(exclude={"embedding"}, exclude_none=True)
    search_id = await create_search(
        conn, kind, params,
        requested_by=requested_by, watchlist_entry_id=watchlist_entry_id,
    )

    # Step 2. The count that makes the rest mean something.
    rarity_count = await vahan.population(conn, description, district)

    # Step 3. Cheap filter first.
    groups: list[list[Candidate]] = []
    attribute_hits = await cand.by_attributes(
        conn, description, since=since, until=until, camera_ids=camera_ids
    )
    groups.append(attribute_hits)

    # Step 4. Fuzzy plate, merged in.
    if description.registration_no:
        groups.append(
            await cand.by_plate(conn, description.registration_no, since=since, until=until)
        )

    # Step 5. Appearance similarity, only over what survived.
    if description.embedding:
        restrict = [c.read_id for c in attribute_hits] or None
        groups.append(
            await cand.by_embedding(
                conn, description.embedding, restrict_to=restrict,
                since=since, until=until,
            )
        )

    if description.caption_query:
        groups.append(
            await cand.by_caption(conn, None, description.caption_query,
                                  since=since, until=until)
        )

    merged = cand.merge(*groups)[: settings.candidate_limit]

    # Steps 6 and 7.
    built, rejected = await route_builder.enumerate_routes(conn, merged)
    scored = scoring.rank(built, rarity_count)
    route_ids = await route_builder.persist(conn, search_id, scored, rejected)

    await conn.execute("UPDATE searches SET status = 'done' WHERE id = $1", search_id)

    result = SearchResult(
        search_id=search_id, description=description, rarity_count=rarity_count,
        candidates=merged, rejected_legs=len(rejected),
        routes=[
            {
                "route_id": str(route_id),
                "rank": rank_index,
                "score": score,
                "competing_count": len(scored),
                "competing_count_capped": route.count_capped,
                "plate_anchored": route.plate_anchored,
                "min_trust": route.min_trust,
                "leg_count": len(route.legs),
                "cameras": [s.camera_id for s in route.sightings],
                "read_ids": [str(s.read_id) for s in route.sightings],
                "gaps_s": [leg.gap_s for leg in route.legs if leg.gap_s],
                "explain": scoring.explain(route, rarity_count),
            }
            for rank_index, (route_id, (route, score, _)) in enumerate(
                zip(route_ids, scored, strict=False), start=1
            )
        ],
    )

    log.info(
        "search_complete", search_id=str(search_id), candidates=len(merged),
        routes=len(scored), rarity=rarity_count, rejected_legs=len(rejected),
    )
    return result


async def by_registration(
    conn: asyncpg.Connection,
    registration_no: str,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    requested_by: uuid.UUID | None = None,
) -> SearchResult:
    """Step 1: a registration number is just a string until VAHAN turns it
    into something searchable."""
    description = await vahan.lookup(conn, registration_no)
    if description is None:
        # Not in VAHAN. The plate itself is still searchable against stored
        # OCR hypotheses, which is worth doing rather than returning nothing.
        description = VehicleDescription(registration_no=registration_no)
        log.warning("vahan_miss_plate_only", registration_no=registration_no)
    else:
        description.registration_no = registration_no

    return await run(
        conn, description, kind=SearchKind.REGISTRATION,
        since=since, until=until, requested_by=requested_by,
        district=description.district,
    )
