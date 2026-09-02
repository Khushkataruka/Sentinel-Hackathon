"""Route scoring. Deterministic, and none of it is a model's opinion of its
own output.

Four terms:

    camera trust    per leg, from the section 6 survey. Weak cameras weaken
                    the whole route -- the minimum, not the mean, because a
                    route is only as good as its worst link.
    rarity          from VAHAN. Forty thousand white Swifts is not a match,
                    it is a category. Common descriptions score down, hard.
    plate anchoring one real plate read is worth more than four description
                    matches, because it contains an identity rather than a
                    kind.
    route length    longer chains score up, but rarity dominates. A
                    four-camera chain for a common car should score BELOW a
                    two-camera chain for a rare one, and the weights below
                    are chosen so it does.

Determinism is not decoration. The audit log is append-only and any alert has
to be rebuildable exactly as it was, which means no randomness, no wall-clock
input, and no floating-point accumulation order that varies with dict
iteration.
"""

from __future__ import annotations

import math

from sentinel.core.types import AlertTier
from sentinel.correlation.routes import RouteCandidate

# Weights. They sum to 1.0 so the score stays in [0, 1] and can be read as a
# proportion rather than an arbitrary magnitude.
W_TRUST = 0.20
W_RARITY = 0.40
W_PLATE = 0.25
W_LENGTH = 0.15

#: Population at which a description stops being evidence. Above this the
#: rarity term is zero: the description names a category, not a vehicle.
RARITY_CEILING = 50_000

#: Route length at which extra cameras stop adding much. Beyond this the
#: length term saturates, so a long chain of weak links cannot outscore a
#: short chain of strong ones.
LENGTH_SATURATION = 5


def rarity_term(match_count: int | None) -> float:
    """1.0 for a unique vehicle, falling to 0.0 at the ceiling.

    Logarithmic, because the difference between 10 and 100 matching vehicles
    matters far more than the difference between 10,000 and 100,000 -- both
    of the latter mean "we have not identified anything".
    """
    if match_count is None or match_count <= 1:
        return 1.0
    if match_count >= RARITY_CEILING:
        return 0.0
    return 1.0 - math.log10(match_count) / math.log10(RARITY_CEILING)


def length_term(leg_count: int) -> float:
    """More corroborating sightings is better, with diminishing returns."""
    if leg_count <= 0:
        return 0.0
    return min(leg_count, LENGTH_SATURATION) / LENGTH_SATURATION


def trust_term(route: RouteCandidate) -> float:
    """The weakest camera on the route.

    Minimum rather than mean, deliberately. A route that depends on one
    thumbnail-resolution analog feed is only as trustworthy as that feed,
    however good the other three cameras were.
    """
    return route.min_trust


def score_route(route: RouteCandidate, rarity_count: int | None) -> float:
    """The route's score, in [0, 1]."""
    terms = (
        W_TRUST * trust_term(route),
        W_RARITY * rarity_term(rarity_count),
        W_PLATE * (1.0 if route.plate_anchored else 0.0),
        W_LENGTH * length_term(len(route.legs)),
    )
    return round(sum(terms), 6)


def explain(route: RouteCandidate, rarity_count: int | None) -> dict[str, float]:
    """The score broken into its parts.

    Shown in the evidence panel. Every claim points at where it came from,
    and a score that cannot be decomposed is a claim that cannot be checked.
    """
    return {
        "trust": round(trust_term(route), 4),
        "trust_weighted": round(W_TRUST * trust_term(route), 4),
        "rarity": round(rarity_term(rarity_count), 4),
        "rarity_weighted": round(W_RARITY * rarity_term(rarity_count), 4),
        "plate_anchored": 1.0 if route.plate_anchored else 0.0,
        "plate_weighted": round(W_PLATE * (1.0 if route.plate_anchored else 0.0), 4),
        "length": round(length_term(len(route.legs)), 4),
        "length_weighted": round(W_LENGTH * length_term(len(route.legs)), 4),
        "total": score_route(route, rarity_count),
    }


def rank(
    routes: list[RouteCandidate], rarity_count: int | None
) -> list[tuple[RouteCandidate, float, int | None]]:
    """Score and order. Ties break on leg count, then on the first sighting
    time, so the order is stable across runs."""
    scored = [(r, score_route(r, rarity_count), rarity_count) for r in routes]
    scored.sort(
        key=lambda item: (
            -item[1],
            -len(item[0].legs),
            item[0].sightings[0].seen_at if item[0].sightings else None,
        )
    )
    return scored


# ---------------------------------------------------------------------------
# Tiering
# ---------------------------------------------------------------------------

#: Below this an alert is not worth an operator's attention.
DISMISS_BELOW = 0.25
#: Above this it goes to the top of the inbox.
PRIORITY_ABOVE = 0.65


def tier(score: float, *, competing_count: int = 1, plate_anchored: bool = False) -> AlertTier:
    """Sort a match into dismiss, review or priority.

    The competing-route count is a direct measure of uncertainty, so it
    demotes: a good score that is one of forty equally good scores is not a
    priority, it is a shortlist. A plate anchor promotes for the opposite
    reason.
    """
    adjusted = score
    if competing_count > 10:
        adjusted *= 0.75
    if competing_count > 40:
        adjusted *= 0.6
    if plate_anchored:
        adjusted = min(1.0, adjusted * 1.2)

    if adjusted >= PRIORITY_ABOVE:
        return AlertTier.PRIORITY
    if adjusted >= DISMISS_BELOW:
        return AlertTier.REVIEW
    return AlertTier.DISMISS
