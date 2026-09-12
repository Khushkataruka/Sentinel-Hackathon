-- Grant every camera every capability, by default and retroactively.
--
-- Reverses the rule 001_schema.sql was built around: a profile used to permit
-- nothing until the section 6 survey measured it, so an unsurveyed camera
-- produced a track and nothing else. From here a profile row permits the full
-- attribute set, ANPR and every violation type unless something explicitly
-- narrows it.
--
-- What is asserted rather than measured, since nothing downstream re-checks:
--   * red_light and wrong_way read needs_scene_context -- a stop line and a
--     signal head. No camera has lane_polygon or lane_count, so that context
--     does not exist for any row this migration touches.
--   * no_seatbelt and phone_use read needs_glass_penetration. Both keep
--     auto_confirm_threshold IS NULL, so each finding still routes to a human;
--     that is the only remaining check on them.
--   * density_viable = true does not create lane geometry. traffic.py still
--     requires lane_length_m before it emits density_vpkm, so density stays
--     NULL until lane_count and lane_polygon are recorded.
--
-- The violations_permitted_check trigger is left in place. It now passes for
-- every type on every camera, but it is what still rejects a code that is not
-- in violation_types at all.
--
-- Kept deliberately as column DEFAULTs rather than a trigger: a DEFAULT is
-- visible in \d camera_profiles, and any INSERT may still narrow a camera by
-- naming the column. If per-camera capability ever matters again, the upgrade
-- path is to drop these four defaults and re-run scripts/survey.py, which
-- writes measured rows through the registry.

BEGIN;

ALTER TABLE camera_profiles
  ALTER COLUMN permitted_attributes
    SET DEFAULT '{colour,type,make,model,features}'::text[],
  ALTER COLUMN permitted_violations
    SET DEFAULT '{illegal_parking,no_helmet,no_seatbelt,phone_use,red_light,triple_riding,wrong_way}'::text[],
  ALTER COLUMN plate_viable   SET DEFAULT true,
  ALTER COLUMN density_viable SET DEFAULT true;

COMMENT ON COLUMN camera_profiles.permitted_attributes IS
  'Description fields this camera may record. Defaults to all of them; see '
  'ALL_ATTRIBUTES in sentinel.core.types, which must agree.';
COMMENT ON COLUMN camera_profiles.permitted_violations IS
  'violation_types.code values this camera may assert. Defaults to every '
  'enabled code, including those needing glass penetration or scene context.';

-- Existing rows. permitted_violations comes from violation_types rather than
-- a literal, so this cannot drift from the catalogue it is checked against.
UPDATE camera_profiles SET
  permitted_attributes = '{colour,type,make,model,features}'::text[],
  permitted_violations = (
    SELECT array_agg(code ORDER BY code) FROM violation_types WHERE enabled
  ),
  plate_viable   = true,
  density_viable = true;

COMMIT;
