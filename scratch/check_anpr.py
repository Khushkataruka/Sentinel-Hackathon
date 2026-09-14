import numpy as np
from pathlib import Path
from sentinel.pipelines.models.anpr import load_plate_reader, validate

print("==================================================")
print("             SENTINEL ANPR CHECK                  ")
print("==================================================")

reader = load_plate_reader()
print(f"Loaded Plate Reader Class: {reader.__class__.__name__}")
print(f"Is Stub Reader?          : {reader.is_stub}")

# Test plate format validator
sample_plates = ["GJ01AB1234", "MH12DE5678", "28BH1234AA", "INVALID123", "12345"]
print("\n--- Testing Indian Registration Validator ---")
for p in sample_plates:
    print(f"Plate '{p}': {'VALID' if validate(p) else 'INVALID'}")

# Test reader on synthetic dummy vehicle crop (100x200 pixels)
dummy_crop = np.full((100, 200, 3), 128, dtype=np.uint8)
reads = reader(dummy_crop, top_k=3)

print("\n--- Test Crop Reading Results ---")
if reads:
    for r in reads:
        print(f"Rank {r.rank}: Plate='{r.text}', Confidence={r.confidence}, ValidFormat={r.valid_format}")
else:
    print("No plates detected in dummy test crop.")

print("\n--------------------------------------------------")
if reader.is_stub:
    print("STATUS: Currently using StubPlateReader.")
    print("To enable Real ANPR:")
    print("  1. Place 'license-plate-finetune-v1m.pt' or 'plate_detect.pt' in var/models/")
    print("  2. EasyOCR and PyTorch will automatically load for real plate extraction.")
else:
    print("STATUS: REAL ANPR ACTIVE using PyTorch + EasyOCR!")
print("==================================================")
