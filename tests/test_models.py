"""The stub models. What matters is that they are deterministic, and that
their output cannot be mistaken for the real thing."""

from __future__ import annotations

import numpy as np
from sentinel.pipelines.models import anpr, captioner, reid, violations


def crop(seed=1, w=128, h=96):
    return np.random.default_rng(seed).integers(0, 255, (h, w, 3), dtype=np.uint8)


def test_the_captioner_is_deterministic():
    image = crop()
    assert captioner.StubCaptioner()(image, "car") == captioner.StubCaptioner()(image, "car")


def test_stub_captions_are_marked_as_stubs():
    """A stub that quietly produced plausible descriptions would be the most
    dangerous thing in the repository."""
    description = captioner.StubCaptioner()(crop(), "car")
    assert description.caption.startswith("[stub]")


def test_the_camera_survey_can_remove_fields_the_model_produced():
    """A thumbnail-resolution camera does not get to claim a model name."""
    full = captioner.StubCaptioner()(crop(), "car")
    restricted = full.restrict_to(["colour"])
    assert restricted.colour == full.colour
    assert restricted.make is None and restricted.model is None


def test_embeddings_are_normalised():
    vector = np.array(reid.StubReID(dim=64)(crop()))
    assert abs(np.linalg.norm(vector) - 1.0) < 1e-5


def test_the_same_crop_always_embeds_the_same():
    image = crop()
    assert reid.StubReID(dim=64)(image) == reid.StubReID(dim=64)(image)


def test_crops_below_the_usable_size_are_rejected():
    tiny = crop(w=16, h=12)
    assert not reid.usable(tiny, "full")
    assert reid.usable(crop(w=128, h=96), "thumbnail")


def test_the_plate_stub_reads_nothing_most_of_the_time():
    """Section 3.1: plates are not legible on most of this estate. A stub
    that returned one every time would make every route look plate-anchored
    and wrongly confident."""
    reader = anpr.StubPlateReader()
    hits = sum(1 for seed in range(200) if reader(crop(seed)))
    assert 0 < hits < 60


def test_plate_format_validation_rejects_garbage():
    assert anpr.validate("GJ01AB1234")
    assert not anpr.validate("XX99ZZ0000")     # not a state code
    assert not anpr.validate("ABC")


def test_a_helmet_verdict_can_be_unknown():
    """None means the camera cannot assess it, which is different from
    'no helmet' and must stay different."""
    riders = violations.StubRiderDetector()(crop())
    assert all(r.helmet in (True, False, None) for r in riders)
    assert all(r.slot >= 1 for r in riders)


def test_the_violation_stub_never_invents_an_unpermitted_type():
    findings = violations.StubViolationDetector()(
        crop(), vehicle_class="motorcycle", permitted=["no_helmet"],
    )
    assert all(f.violation_type == "no_helmet" for f in findings)


def test_no_permitted_types_means_no_findings():
    findings = violations.StubViolationDetector()(
        crop(), vehicle_class="car", permitted=[],
    )
    assert findings == []
