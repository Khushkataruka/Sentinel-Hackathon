"""Description pipeline.

Runs when the camera has any permitted attributes at all. Produces the
structured fields and a free-text caption in one pass, then encodes the
caption into a small vector.

Two indexes sit on that caption: a vector index for meaning and a trigram
index for literal substrings. An operator can search "white hatchback with a
roof carrier" and get results, which the four attribute columns cannot
express. That search path did not exist before the caption did.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from sentinel.core import queue
from sentinel.core.db import encode_vector
from sentinel.core.types import Pipeline
from sentinel.pipelines.base import PipelineWorker
from sentinel.pipelines.models import captioner as captioner_models


class DescribeWorker(PipelineWorker):
    pipeline = Pipeline.DESCRIBE
    model_role = "describe"

    async def setup(self) -> None:
        self.captioner = captioner_models.load_captioner()
        self.embedder = captioner_models.load_caption_embedder()
        self.caption_embed_model_id = await self._caption_embed_model_id()

    async def _caption_embed_model_id(self) -> int | None:
        from sentinel.core.db import acquire

        async with acquire() as conn:
            return await conn.fetchval(
                "SELECT id FROM model_versions WHERE role = 'caption_embed' "
                "ORDER BY registered_at DESC LIMIT 1"
            )

    async def process(
        self, conn, job: queue.Job, crop: np.ndarray
    ) -> dict[str, Any] | None:
        vehicle_class = job.payload.get("class", "car")
        permitted = job.payload.get("permitted_attributes", [])

        description = self.captioner(crop, vehicle_class)
        # The camera's survey decides what may be recorded, not the model.
        description = description.restrict_to(permitted)

        caption_vector = self.embedder(description.caption) if description.caption else None

        await conn.execute(
            """
            UPDATE sightings
               SET colour = $2, vtype = $3, make = $4, model = $5,
                   features = $6, caption = $7,
                   caption_embedding = $8::vector,
                   describe_model_id = $9
             WHERE read_id = $1
            """,
            job.read_id,
            description.colour,
            description.vtype,
            description.make,
            description.model,
            description.features,
            description.caption or None,
            encode_vector(caption_vector),
            self.model_id,
        )

        return {
            "colour": description.colour,
            "make": description.make,
            "fields_dropped": len(permitted) == 0,
        }
