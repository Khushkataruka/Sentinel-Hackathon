"""VAHAN: the lookup, and the population count.

Two jobs, and the second is the one that matters.

The obvious job: the evaluation hands us a registration number, which is just
a string. VAHAN turns it into colour, make, model and vehicle class, which is
the only form this system can actually search for.

The less obvious job: VAHAN can tell us how many vehicles match that
description. If there are forty thousand white Swifts registered in the
district, that number is the difference between a match meaning something and
a match meaning nothing. With the attribute confidences gone, this count is
the main thing separating a strong description match from a weak one.

For the evaluation both come from the local stand-in tables. The real
integration is an HLD concern; only this module changes.
"""

from __future__ import annotations

import asyncpg
from sentinel.core.logging import get_logger
from sentinel.core.models import VehicleDescription

log = get_logger(__name__)

WILDCARD = "any"


async def lookup(conn: asyncpg.Connection, registration_no: str) -> VehicleDescription | None:
    """Registration number -> description."""
    row = await conn.fetchrow(
        """
        SELECT registration_no, colour, make, model, vtype, district
          FROM vahan_vehicles WHERE upper(registration_no) = upper($1)
        """,
        registration_no.strip(),
    )
    if row is None:
        log.info("vahan_miss", registration_no=registration_no)
        return None
    return VehicleDescription(**dict(row))


async def population(
    conn: asyncpg.Connection,
    description: VehicleDescription,
    district: str | None = None,
) -> int:
    """How many vehicles match this description here.

    Falls back through progressively coarser cached rows: exact, then without
    the model, then without the make, and so on. 'any' is the wildcard,
    because NULL cannot sit in a primary key and a three-way NULL match in a
    join is a bug factory.

    A miss returns a large number, which scores the match DOWN. Erring
    towards "this description is common" is the safe direction: it produces a
    weak match rather than a confident wrong one.
    """
    district = (district or description.district or WILDCARD).lower()
    colour = (description.colour or WILDCARD).lower()
    vtype = (description.vtype or WILDCARD).lower()
    make = (description.make or WILDCARD).lower()
    model = (description.model or WILDCARD).lower()

    attempts = [
        (district, colour, vtype, make, model),
        (district, colour, vtype, make, WILDCARD),
        (district, colour, vtype, WILDCARD, WILDCARD),
        (district, colour, WILDCARD, WILDCARD, WILDCARD),
        (WILDCARD, colour, vtype, make, model),
        (WILDCARD, colour, WILDCARD, WILDCARD, WILDCARD),
        (WILDCARD, WILDCARD, WILDCARD, WILDCARD, WILDCARD),
    ]

    for key in attempts:
        count = await conn.fetchval(
            """
            SELECT match_count FROM rarity
             WHERE district = $1 AND colour = $2 AND vtype = $3
               AND make = $4 AND model = $5
            """,
            *key,
        )
        if count is not None:
            return int(count)

    # Nothing cached at any level. Count the stand-in table directly.
    counted = await conn.fetchval(
        """
        SELECT count(*) FROM vahan_vehicles
         WHERE ($1 = 'any' OR lower(district) = $1)
           AND ($2 = 'any' OR lower(colour) = $2)
           AND ($3 = 'any' OR lower(vtype) = $3)
           AND ($4 = 'any' OR lower(make) = $4)
           AND ($5 = 'any' OR lower(model) = $5)
        """,
        district, colour, vtype, make, model,
    )
    if counted:
        return int(counted)

    log.warning("rarity_miss", colour=colour, vtype=vtype, make=make, model=model)
    return 1_000_000


async def cache_population(
    conn: asyncpg.Connection, description: VehicleDescription, district: str, count: int
) -> None:
    await conn.execute(
        """
        INSERT INTO rarity (district, colour, vtype, make, model, match_count, source)
        VALUES ($1,$2,$3,$4,$5,$6,'vahan_stub')
        ON CONFLICT (district, colour, vtype, make, model)
        DO UPDATE SET match_count = EXCLUDED.match_count, fetched_at = now()
        """,
        district.lower(),
        (description.colour or WILDCARD).lower(),
        (description.vtype or WILDCARD).lower(),
        (description.make or WILDCARD).lower(),
        (description.model or WILDCARD).lower(),
        count,
    )
