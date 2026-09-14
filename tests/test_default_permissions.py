"""The default capability grant.

Every camera now permits everything, which means the two lists in
sentinel.core.types are the only thing standing between a profile and a
pipeline. They are hardcoded, so the risk is drift: a violation type added to
violation_types and not to ALL_VIOLATIONS is a type no camera can assert, and
the failure looks like a trigger exception at insert rather than a config
mistake. The database test below is the thing that catches it.
"""

from __future__ import annotations

from sentinel.core.models import CameraProfileIn
from sentinel.core.types import ALL_ATTRIBUTES, ALL_VIOLATIONS


def test_an_empty_profile_permits_everything():
    """The whole point of the change: a profile that names no capability gets
    all of them, rather than none."""
    profile = CameraProfileIn()

    assert profile.permitted_attributes == ALL_ATTRIBUTES
    assert profile.permitted_violations == ALL_VIOLATIONS
    assert profile.plate_viable
    assert profile.density_viable


def test_a_profile_may_still_narrow_a_camera():
    """Defaults, not a floor. A caller that names the fields still wins, which
    is what makes the grant reversible per camera."""
    profile = CameraProfileIn(permitted_attributes=["colour"], plate_viable=False)

    assert profile.permitted_attributes == ["colour"]
    assert not profile.plate_viable
    assert profile.permitted_violations == ALL_VIOLATIONS  # untouched field


def test_defaults_are_not_shared_between_instances():
    """A bare list default on a pydantic field would be one list shared by
    every profile, so mutating one camera's grant would mutate all of them."""
    first, second = CameraProfileIn(), CameraProfileIn()
    first.permitted_attributes.append("windscreen_sticker")

    assert second.permitted_attributes == ALL_ATTRIBUTES


def test_every_attribute_survives_restrict_to():
    """ALL_ATTRIBUTES has to spell the fields the way restrict_to reads them.
    'type' against the sightings column `vtype` is the one that bites."""
    from sentinel.pipelines.models.captioner import Description

    full = Description(colour="red", vtype="car", make="Tata", model="Nexon",
                       features=["roof rack"], caption="c")
    kept = full.restrict_to(ALL_ATTRIBUTES)

    assert kept == full, "an attribute in ALL_ATTRIBUTES is spelled wrong"


async def test_all_violations_matches_the_catalogue(db):
    """The hardcoded list against the table the trigger actually reads."""
    codes = [r["code"] for r in await db.fetch(
        "SELECT code FROM violation_types WHERE enabled ORDER BY code")]

    assert sorted(ALL_VIOLATIONS) == codes


async def test_every_camera_permits_everything(db):
    """Migration 005 applied, and nothing has written a narrower row since."""
    codes = [r["code"] for r in await db.fetch(
        "SELECT code FROM violation_types WHERE enabled ORDER BY code")]

    gaps = await db.fetch(
        """
        SELECT camera_id, permitted_attributes, permitted_violations,
               plate_viable, density_viable
          FROM camera_profiles
         WHERE NOT (permitted_attributes @> $1::text[])
            OR NOT (permitted_violations @> $2::text[])
            OR NOT plate_viable
            OR NOT density_viable
        """,
        ALL_ATTRIBUTES, codes,
    )

    assert not gaps, f"{len(gaps)} profile(s) short of the full grant: " \
                     f"{[r['camera_id'] for r in gaps]}"
