"""Finding candidate sightings.

Filter first, rank second. The attribute filter is an index lookup and costs
nothing; the vector comparison is the expensive part and only runs on what
survives. That ordering is the difference between a search that returns in a
second and one that scans the whole sightings table.

Five stages, merged:

    attribute filter  cheap    colour, type, make, on a partial index
    rarity lookup     cheap    how many vehicles match this description
    vector search     moderate appearance similarity over the survivors
    caption search    moderate free-text, when the operator searched that way
    fuzzy plate       cheap    confusion-weighted, merged in

A candidate found by more than one route is not scored twice. It is marked as
matched on several signals, which is stronger evidence than any one of them.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import asyncpg
from sentinel.core.config import settings
from sentinel.core.db import encode_vector
from sentinel.core.logging import get_logger
from sentinel.core.models import Candidate, VehicleDescription
from sentinel.correlation import plates

log = get_logger(__name__)


async def by_attributes(
    conn: asyncpg.Connection,
    description: VehicleDescription,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    camera_ids: list[str] | None = None,
    limit: int | None = None,
) -> list[Candidate]:
    """The cheap filter. Uses the partial index on (colour, vtype, make, model).

    Every attribute is optional: a description with only a colour still
    filters, it just filters weakly, and the rarity count will say so.

    A description with NO attributes at all returns nothing rather than
    everything. That case arises when a registration number is not in VAHAN:
    there is no description to search for, and matching the entire estate
    would be worse than useless -- it produces thousands of candidates, an
    explosion of physically-possible routes, and a top result chosen by
    nothing at all. The plate search still runs alongside this, so a VAHAN
    miss can still be found by its stored OCR hypotheses.
    """
    if not any((description.colour, description.vtype, description.make, description.model)):
        log.info(
            "empty_description_filter_skipped",
            reason="no attributes to filter on; not returning the whole estate",
        )
        return []

    rows = await conn.fetch(
        """
        SELECT s.read_id, s.camera_id, s.seen_at, s.plate_text,
               COALESCE(p.trust_level, 0.5) AS trust_level
          FROM sightings s
          LEFT JOIN camera_profiles p ON p.camera_id = s.camera_id
         WHERE ($1::text IS NULL OR lower(s.colour) = lower($1))
           AND ($2::text IS NULL OR lower(s.vtype)  = lower($2))
           AND ($3::text IS NULL OR lower(s.make)   = lower($3))
           AND ($4::text IS NULL OR lower(s.model)  = lower($4))
           AND ($5::timestamptz IS NULL OR s.seen_at >= $5)
           AND ($6::timestamptz IS NULL OR s.seen_at <= $6)
           AND ($7::text[] IS NULL OR s.camera_id = ANY($7))
         ORDER BY s.seen_at DESC
         LIMIT $8
        """,
        description.colour, description.vtype, description.make, description.model,
        since, until, camera_ids, limit or settings.candidate_limit,
    )
    return [
        Candidate(
            read_id=r["read_id"], camera_id=r["camera_id"], seen_at=r["seen_at"],
            score=0.0, matched_on=["attribute"], plate_text=r["plate_text"],
            trust_level=float(r["trust_level"]),
        )
        for r in rows
    ]


async def by_embedding(
    conn: asyncpg.Connection,
    embedding: list[float],
    *,
    restrict_to: list[uuid.UUID] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int | None = None,
) -> list[Candidate]:
    """KNN over the appearance vectors.

    Cosine, to match the re-id literature and the index. The partial HNSW
    index means rows without an embedding are invisible here, which is why
    the embed pipeline leaves the column null rather than writing zeros.

    restrict_to is what makes this cheap: pass the read_ids that survived the
    attribute filter and the scan is bounded.
    """
    rows = await conn.fetch(
        """
        SELECT s.read_id, s.camera_id, s.seen_at, s.plate_text,
               1 - (s.embedding <=> $1::vector) AS similarity,
               COALESCE(p.trust_level, 0.5) AS trust_level
          FROM sightings s
          LEFT JOIN camera_profiles p ON p.camera_id = s.camera_id
         WHERE s.embedding IS NOT NULL
           AND ($2::uuid[] IS NULL OR s.read_id = ANY($2))
           AND ($3::timestamptz IS NULL OR s.seen_at >= $3)
           AND ($4::timestamptz IS NULL OR s.seen_at <= $4)
         ORDER BY s.embedding <=> $1::vector
         LIMIT $5
        """,
        encode_vector(embedding), restrict_to, since, until,
        limit or settings.knn_limit,
    )
    return [
        Candidate(
            read_id=r["read_id"], camera_id=r["camera_id"], seen_at=r["seen_at"],
            score=float(r["similarity"]), matched_on=["vector"],
            plate_text=r["plate_text"], trust_level=float(r["trust_level"]),
        )
        for r in rows
    ]


async def by_caption(
    conn: asyncpg.Connection,
    caption_embedding: list[float] | None,
    caption_text: str | None,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int | None = None,
) -> list[Candidate]:
    """Free-text search over the generated caption.

    Two indexes, two jobs. The vector index answers "white hatchback with a
    roof carrier" by meaning. The trigram index answers a literal substring.
    Both are used when both are available, because operators type both kinds
    of thing.
    """
    limit = limit or settings.knn_limit
    results: dict[uuid.UUID, Candidate] = {}

    if caption_embedding:
        rows = await conn.fetch(
            """
            SELECT s.read_id, s.camera_id, s.seen_at, s.plate_text,
                   1 - (s.caption_embedding <=> $1::vector) AS similarity,
                   COALESCE(p.trust_level, 0.5) AS trust_level
              FROM sightings s
              LEFT JOIN camera_profiles p ON p.camera_id = s.camera_id
             WHERE s.caption_embedding IS NOT NULL
               AND ($2::timestamptz IS NULL OR s.seen_at >= $2)
               AND ($3::timestamptz IS NULL OR s.seen_at <= $3)
             ORDER BY s.caption_embedding <=> $1::vector
             LIMIT $4
            """,
            encode_vector(caption_embedding), since, until, limit,
        )
        for r in rows:
            results[r["read_id"]] = Candidate(
                read_id=r["read_id"], camera_id=r["camera_id"], seen_at=r["seen_at"],
                score=float(r["similarity"]), matched_on=["caption"],
                plate_text=r["plate_text"], trust_level=float(r["trust_level"]),
            )

    if caption_text:
        rows = await conn.fetch(
            """
            SELECT s.read_id, s.camera_id, s.seen_at, s.plate_text,
                   similarity(s.caption, $1) AS sim,
                   COALESCE(p.trust_level, 0.5) AS trust_level
              FROM sightings s
              LEFT JOIN camera_profiles p ON p.camera_id = s.camera_id
             WHERE s.caption IS NOT NULL AND s.caption %% $1
               AND ($2::timestamptz IS NULL OR s.seen_at >= $2)
               AND ($3::timestamptz IS NULL OR s.seen_at <= $3)
             ORDER BY sim DESC
             LIMIT $4
            """,
            caption_text, since, until, limit,
        )
        for r in rows:
            existing = results.get(r["read_id"])
            if existing:
                existing.score = max(existing.score, float(r["sim"]))
                if "caption_text" not in existing.matched_on:
                    existing.matched_on.append("caption_text")
            else:
                results[r["read_id"]] = Candidate(
                    read_id=r["read_id"], camera_id=r["camera_id"], seen_at=r["seen_at"],
                    score=float(r["sim"]), matched_on=["caption_text"],
                    plate_text=r["plate_text"], trust_level=float(r["trust_level"]),
                )

    return list(results.values())


async def by_plate(
    conn: asyncpg.Connection,
    registration_no: str,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    threshold: float = 0.82,
    limit: int | None = None,
) -> list[Candidate]:
    """Confusion-weighted plate matching over every stored hypothesis.

    Searches plate_hypotheses, not just sightings.plate_text: keeping the
    alternatives is exactly what makes this work. The trigram index does the
    coarse narrowing, then the weighted distance decides.
    """
    pattern = plates.trigram_prefilter(registration_no)
    rows = await conn.fetch(
        """
        SELECT h.read_id, h.plate, h.confidence, h.rank,
               s.camera_id, s.seen_at, s.plate_text,
               COALESCE(p.trust_level, 0.5) AS trust_level
          FROM plate_hypotheses h
          JOIN sightings s ON s.read_id = h.read_id
          LEFT JOIN camera_profiles p ON p.camera_id = s.camera_id
         WHERE h.plate ILIKE $1
           AND ($2::timestamptz IS NULL OR s.seen_at >= $2)
           AND ($3::timestamptz IS NULL OR s.seen_at <= $3)
         LIMIT $4
        """,
        pattern, since, until, (limit or settings.candidate_limit) * 4,
    )

    best: dict[uuid.UUID, Candidate] = {}
    for r in rows:
        score = plates.similarity(registration_no, r["plate"])
        if score < threshold:
            continue
        existing = best.get(r["read_id"])
        if existing is None or score > existing.score:
            best[r["read_id"]] = Candidate(
                read_id=r["read_id"], camera_id=r["camera_id"], seen_at=r["seen_at"],
                score=score, matched_on=["plate"], plate_text=r["plate"],
                trust_level=float(r["trust_level"]),
            )
    return list(best.values())


def merge(*groups: list[Candidate]) -> list[Candidate]:
    """Combine candidate sets, keeping the best score per sighting and
    recording every signal that found it.

    A sighting matched on both appearance and a plate read is stronger
    evidence than one matched on either alone, and the scorer uses that.
    """
    merged: dict[uuid.UUID, Candidate] = {}
    for group in groups:
        for candidate in group:
            existing = merged.get(candidate.read_id)
            if existing is None:
                merged[candidate.read_id] = candidate.model_copy(deep=True)
                continue
            existing.score = max(existing.score, candidate.score)
            for signal in candidate.matched_on:
                if signal not in existing.matched_on:
                    existing.matched_on.append(signal)
    return sorted(merged.values(), key=lambda c: c.seen_at)
