-- The crop is not the bbox.
--
-- sightings.bbox is the detector's box for the vehicle. The crop written to
-- crop_ref is deliberately larger: a COCO 'motorcycle' box is the machine
-- and not the person on it, so a tight crop excludes the head that a
-- no_helmet finding is about. crop_bbox records where the crop was taken
-- from, which is the only thing that makes a coordinate measured inside the
-- crop convertible back to the frame.
--
-- Nullable: rows written before this migration have no record of it.

BEGIN;

ALTER TABLE sightings
    ADD COLUMN IF NOT EXISTS crop_bbox integer[]
        CHECK (crop_bbox IS NULL OR cardinality(crop_bbox) = 4);

COMMENT ON COLUMN sightings.bbox IS
    'Vehicle box in FRAME pixels (x1, y1, x2, y2).';
COMMENT ON COLUMN sightings.crop_bbox IS
    'Region crop_ref was cut from, in FRAME pixels. Contains bbox. NULL before 004.';
COMMENT ON COLUMN sighting_riders.bbox IS
    'Rider box in CROP pixels, relative to the crop_bbox origin -- not frame pixels.';

COMMIT;
