"""Appearance embedding pipeline.

Separate model, separate pass, separate training objective from the
description. Runs wherever the crop is above the minimum usable size for the
camera's resolution class -- below that the vector is noise with a norm, and
a noisy vector in an HNSW index is worse than an absent one because the
index will happily return it.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from sentinel.core import queue
from sentinel.core.db import encode_vector
from sentinel.core.types import Pipeline, PipelineStatus
from sentinel.pipelines.base import PipelineWorker
from sentinel.pipelines.models import reid as reid_models


class EmbedWorker(PipelineWorker):
    pipeline = Pipeline.EMBED
    model_role = "embed"

    async def setup(self) -> None:
        self.encoder = reid_models.load_reid()

    async def process(
        self, conn, job: queue.Job, crop: np.ndarray
    ) -> dict[str, Any] | None:
        resolution_class = await conn.fetchval(
            "SELECT resolution_class FROM camera_profiles WHERE camera_id = $1",
            job.camera_id,
        ) or "thumbnail"

        if not reid_models.usable(crop, resolution_class):
            # Not a failure: this camera cannot produce a usable appearance
            # vector for a crop this small. Mark it skipped so correlation
            # stops waiting, and leave the column null so the partial HNSW
            # index never sees it.
            await self._set_status(conn, job.read_id, PipelineStatus.SKIPPED)
            return {"skipped": "crop below usable size",
                    "resolution_class": resolution_class}

        vector = self.encoder(crop)
        await conn.execute(
            """
            UPDATE sightings SET embedding = $2::vector, embed_model_id = $3
             WHERE read_id = $1
            """,
            job.read_id, encode_vector(vector), self.model_id,
        )
        return {"dim": len(vector)}
