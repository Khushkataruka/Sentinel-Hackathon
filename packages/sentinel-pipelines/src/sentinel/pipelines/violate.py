"""Traffic violation pipeline.

Zero or more violations per sighting. Runs when the camera has any permitted
violation types, and only attempts the types in that list.

Order matters here: rider rows are written FIRST, because violations carries
a composite foreign key (read_id, rider_slot) into sighting_riders. A
no_helmet violation naming slot 2 cannot be inserted before slot 2 exists.

The database also enforces that a camera may only assert a type its survey
permits. We do not pre-check that and skip -- we let the insert fail. A
pipeline bug that tries to write a seatbelt violation from a camera never
surveyed for it should produce an exception someone reads, not a silently
dropped row.

What this platform does NOT do is issue challans. Automatic fining requires
an identified owner, and section 3.1 says plainly that plates are not
readable on this estate. Issuing a penalty against a vehicle identified by
appearance would mean fining the wrong person, at scale, with an audit trail
proving it.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from sentinel.core import media, queue
from sentinel.core.logging import get_logger
from sentinel.core.types import Pipeline, PipelineStatus, ReviewStatus
from sentinel.pipelines.base import PipelineWorker
from sentinel.pipelines.models import violations as violation_models

log = get_logger(__name__)


class ViolateWorker(PipelineWorker):
    pipeline = Pipeline.VIOLATE
    model_role = "violation"

    TWO_WHEELER = {"motorcycle", "bicycle"}

    async def setup(self) -> None:
        self.rider_detector = violation_models.load_rider_detector()
        self.detector = violation_models.load_violation_detector()

    async def _load_type_rules(self, conn, codes: list[str]) -> dict[str, dict]:
        rows = await conn.fetch(
            """
            SELECT code, subject, applies_to, is_temporal,
                   auto_confirm_threshold, review_threshold
              FROM violation_types
             WHERE code = ANY($1) AND enabled
            """,
            codes,
        )
        return {r["code"]: dict(r) for r in rows}

    async def process(
        self, conn, job: queue.Job, crop: np.ndarray
    ) -> dict[str, Any] | None:
        permitted = job.payload.get("permitted_violations", [])
        vehicle_class = job.payload.get("class", "car")

        if not permitted:
            await self._set_status(conn, job.read_id, PipelineStatus.SKIPPED)
            return {"skipped": "camera permits no violation types"}

        rules = await self._load_type_rules(conn, permitted)

        # Intersect the camera's permitted types with the types that apply to
        # this vehicle class. An empty applies_to means any class.
        applicable = [
            code for code, rule in rules.items()
            if not rule["applies_to"] or vehicle_class in rule["applies_to"]
        ]
        if not applicable:
            await self._set_status(conn, job.read_id, PipelineStatus.SKIPPED)
            return {"skipped": f"no permitted type applies to {vehicle_class}"}

        sighting = await conn.fetchrow(
            "SELECT seen_at, track_start_at, track_end_at FROM sightings WHERE read_id = $1",
            job.read_id,
        )

        # Riders first: the composite FK depends on them existing.
        riders: list[violation_models.RiderBox] = []
        if vehicle_class in self.TWO_WHEELER:
            riders = self.rider_detector(crop)
            await conn.execute(
                "DELETE FROM sighting_riders WHERE read_id = $1", job.read_id
            )
            for rider in riders:
                await conn.execute(
                    """
                    INSERT INTO sighting_riders
                        (read_id, slot, bbox, helmet, helmet_conf, detector_id)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    job.read_id, rider.slot, list(rider.bbox),
                    rider.helmet, rider.helmet_conf, self.model_id,
                )

        findings = self.detector(
            crop,
            vehicle_class=vehicle_class,
            permitted=applicable,
            track_context={
                "track_start_pts": None,
                "track_end_pts": None,
                "riders": len(riders),
            },
        )

        written = 0
        discarded = 0

        for finding in findings:
            rule = rules.get(finding.violation_type)
            if rule is None:
                continue

            # Below the review threshold, discard silently. Recording a
            # finding nobody will ever look at is how a review queue becomes
            # something operators learn to ignore.
            if finding.confidence < rule["review_threshold"]:
                discarded += 1
                continue

            auto = rule["auto_confirm_threshold"]
            review_status = (
                ReviewStatus.AUTO_CONFIRMED
                if auto is not None and finding.confidence >= auto
                else ReviewStatus.PENDING_REVIEW
            )

            rider_slot = None
            if rule["subject"] == "rider":
                # Attribute to the rider the finding names, or to the first
                # rider without a helmet, or slot 1.
                rider_slot = finding.rider_slot
                if rider_slot is None:
                    no_helmet = [r.slot for r in riders if r.helmet is False]
                    rider_slot = no_helmet[0] if no_helmet else (riders[0].slot if riders else None)
                if rider_slot is None:
                    # A rider-subject violation with no rider row is not
                    # assertable. Skip rather than write a dangling claim.
                    discarded += 1
                    continue

            evidence_ref = None
            if finding.bbox:
                path = media.evidence_path(
                    job.camera_id, sighting["seen_at"],
                    f"{job.read_id}-{finding.violation_type}",
                )
                import cv2

                cv2.imwrite(str(path), crop)
                evidence_ref = media.relative(path)

            try:
                await conn.execute(
                    """
                    INSERT INTO violations (
                        read_id, camera_id, violation_type, rider_slot, confidence,
                        seen_at, window_start, window_end, evidence_ref, evidence_bbox,
                        detector_id, review_status, details)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                    """,
                    job.read_id, job.camera_id, finding.violation_type, rider_slot,
                    finding.confidence, sighting["seen_at"],
                    sighting["track_start_at"] if rule["is_temporal"] else None,
                    sighting["track_end_at"] if rule["is_temporal"] else None,
                    evidence_ref,
                    list(finding.bbox) if finding.bbox else None,
                    self.model_id, review_status.value, finding.details,
                )
                written += 1
            except Exception as exc:
                # The permitted-violations trigger. Loud, because it means a
                # pipeline tried to assert something the survey forbade.
                log.error(
                    "violation_rejected", read_id=str(job.read_id),
                    camera=job.camera_id, type=finding.violation_type, error=str(exc),
                )
                raise

        return {"riders": len(riders), "written": written, "below_threshold": discarded}
