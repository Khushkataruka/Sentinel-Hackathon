"""Plate reading pipeline.

Only runs on cameras the survey marked plate_viable, which on this estate is
a small minority.

The top few hypotheses go into plate_hypotheses as one row each, not as a
JSON array on the sighting. A JSON array cannot carry a trigram index; rows
can, and the confusion-weighted fuzzy matching in correlation runs on that
index.

plate_conf is one of only two confidence numbers this platform kept. It
survives because it sets the cutoff for fuzzy matching -- a threshold on a
decision, not a claim about who the vehicle is.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from sentinel.core import queue
from sentinel.core.types import Pipeline, PipelineStatus
from sentinel.pipelines.base import PipelineWorker
from sentinel.pipelines.models import anpr

TOP_K = 3


class PlateWorker(PipelineWorker):
    pipeline = Pipeline.PLATE
    model_role = "plate_ocr"

    async def setup(self) -> None:
        self.reader = anpr.load_plate_reader()

    async def process(
        self, conn, job: queue.Job, crop: np.ndarray
    ) -> dict[str, Any] | None:
        reads = self.reader(crop, top_k=TOP_K)
        # Format validation rejects a large amount of OCR garbage before it
        # reaches the database.
        reads = [r for r in reads if r.valid_format]

        if not reads:
            # No plate found is the normal case on this estate, not an error.
            await self._set_status(conn, job.read_id, PipelineStatus.SKIPPED)
            return {"reads": 0}

        reads.sort(key=lambda r: -r.confidence)
        best = reads[0]

        await conn.execute(
            "UPDATE sightings SET plate_text = $2, plate_conf = $3 WHERE read_id = $1",
            job.read_id, best.text, best.confidence,
        )
        # Rewrite rather than append: a retried job must not stack duplicate
        # ranks against the primary key.
        await conn.execute("DELETE FROM plate_hypotheses WHERE read_id = $1", job.read_id)
        for rank, read in enumerate(reads[:TOP_K], start=1):
            await conn.execute(
                """
                INSERT INTO plate_hypotheses (read_id, rank, plate, confidence)
                VALUES ($1, $2, $3, $4)
                """,
                job.read_id, rank, read.text, read.confidence,
            )

        return {"best": best.text, "conf": best.confidence, "hypotheses": len(reads)}
