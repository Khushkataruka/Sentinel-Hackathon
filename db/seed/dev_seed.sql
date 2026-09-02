-- Development seed. Idempotent: safe to re-run.
-- Nothing here is evaluation data. It exists so the services have something
-- to point at before any real camera is onboarded.

BEGIN;

INSERT INTO departments (code, name) VALUES
  ('HOME', 'Home Department'),
  ('FCS',  'Food and Civil Supplies'),
  ('RTO',  'Regional Transport Office')
ON CONFLICT (code) DO NOTHING;

INSERT INTO users (username, display_name, department_id, role)
SELECT 'admin', 'Platform Administrator', d.id, 'admin'
FROM departments d WHERE d.code = 'HOME'
ON CONFLICT (username) DO NOTHING;

INSERT INTO users (username, display_name, department_id, role)
SELECT 'operator1', 'Control Room Operator', d.id, 'operator'
FROM departments d WHERE d.code = 'HOME'
ON CONFLICT (username) DO NOTHING;

INSERT INTO users (username, display_name, department_id, role)
SELECT 'auditor1', 'Auditor', d.id, 'auditor'
FROM departments d WHERE d.code = 'HOME'
ON CONFLICT (username) DO NOTHING;

-- Model registry. weights_sha256 is null until a real export is registered:
-- the column records what actually ran, so a placeholder hash would be a lie.
INSERT INTO model_versions (role, name, version, output_dim, runtime, notes) VALUES
  ('detect',        'yolov8n',          '0.0.0-stub', NULL, 'onnx-cpu', 'placeholder; replace on first real export'),
  ('track',         'bytetrack',        '1.0.0',      NULL, 'python',   'in-process, no weights'),
  ('describe',      'florence-2-base',  '0.0.0-stub', NULL, 'onnx-cpu', 'placeholder'),
  ('caption_embed', 'minilm-l6-v2',     '0.0.0-stub', 384,  'onnx-cpu', 'placeholder'),
  ('embed',         'reid-r50',         '0.0.0-stub', 512,  'onnx-cpu', 'placeholder; MUST match sightings.embedding dim'),
  ('plate_detect',  'plate-yolo',       '0.0.0-stub', NULL, 'onnx-cpu', 'placeholder'),
  ('plate_ocr',     'plate-crnn',       '0.0.0-stub', NULL, 'onnx-cpu', 'placeholder'),
  ('violation',     'violation-multi',  '0.0.0-stub', NULL, 'onnx-cpu', 'placeholder')
ON CONFLICT (role, name, version) DO NOTHING;

-- A handful of rarity rows so scoring has something to divide by before the
-- VAHAN stand-in is populated. 'any' is the wildcard, per the schema comment.
INSERT INTO rarity (district, colour, vtype, make, model, match_count, source) VALUES
  ('any', 'any',   'any',        'any',    'any', 1000000, 'seed'),
  ('any', 'white', 'hatchback',  'maruti', 'swift', 41200, 'seed'),
  ('any', 'white', 'any',        'any',    'any',  380000, 'seed'),
  ('any', 'red',   'hatchback',  'maruti', 'swift',  1900, 'seed')
ON CONFLICT (district, colour, vtype, make, model) DO NOTHING;

COMMIT;
