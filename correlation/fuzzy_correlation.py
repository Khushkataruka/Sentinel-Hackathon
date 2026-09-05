"""Fuzzy text-based vehicle correlation using a Mamdani FIS.

Usage:
    python fuzzy_correlation.py correlation-input.json fuzzy-output.json

No third-party packages are required. The result includes fuzzy memberships,
rule activations, aggregated output membership, and centroid defuzzification.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path


STOP_WORDS = {"a", "an", "the", "with", "and", "on", "of", "to", "in", "is", "too", "further"}


def clamp(value):
    return max(0.0, min(1.0, value))


def triangle(value, left, peak, right):
    """Triangular membership function."""
    if value <= left or value >= right:
        return 0.0
    if value == peak:
        return 1.0
    return (value - left) / (peak - left) if value < peak else (right - value) / (right - peak)


def trapezoid(value, left, left_top, right_top, right):
    """Trapezoidal membership function."""
    if value <= left or value >= right:
        return 0.0
    if left_top <= value <= right_top:
        return 1.0
    return (value - left) / (left_top - left) if value < left_top else (right - value) / (right - right_top)


def memberships(value):
    """Input memberships: low, medium, high, on the [0, 1] universe."""
    return {
        "low": round(trapezoid(value, -0.01, 0, 0.25, 0.50), 4),
        "medium": round(triangle(value, 0.25, 0.55, 0.82), 4),
        "high": round(trapezoid(value, 0.55, 0.75, 1.0, 1.01), 4),
    }


def output_membership(score):
    """Output memberships over the 0–100 confidence universe."""
    return {
        "low": trapezoid(score, -1, 0, 25, 48),
        "review": triangle(score, 32, 55, 78),
        "strong": trapezoid(score, 62, 78, 100, 101),
    }


def tokens(text):
    return [word for word in re.findall(r"[a-z0-9]+", (text or "").lower()) if word not in STOP_WORDS]


def text_similarity(expected, observed):
    """Fuzzy textual similarity: token overlap plus edit-distance similarity.

    Token overlap rewards meaningful shared descriptors such as 'white',
    'maruti', 'swift', and 'hatchback'. SequenceMatcher tolerates OCR and
    description variations such as 'colour' vs 'color'.
    """
    expected_tokens, observed_tokens = tokens(expected), tokens(observed)
    if not observed_tokens:
        return 0.0, {"token_overlap": 0.0, "edit_similarity": 0.0}
    expected_counts, observed_counts = Counter(expected_tokens), Counter(observed_tokens)
    overlap = sum((expected_counts & observed_counts).values()) / max(len(expected_tokens), 1)
    edit = SequenceMatcher(None, " ".join(expected_tokens), " ".join(observed_tokens)).ratio()
    score = clamp(0.70 * overlap + 0.30 * edit)
    return score, {"token_overlap": round(overlap, 4), "edit_similarity": round(edit, 4)}


def dot(left, right):
    numerator = sum(a * b for a, b in zip(left, right))
    left_size = sum(a * a for a in left) ** 0.5
    right_size = sum(b * b for b in right) ** 0.5
    return numerator / (left_size * right_size) if left_size and right_size else 0.0


def fuzzy_inference(text_score, trust, plate, visual):
    """Mamdani FIS with max–min inference and centroid defuzzification.

    Rules deliberately give plate evidence precedence, while visual and textual
    evidence must agree when a readable plate is unavailable.
    """
    text, camera, plate_m, appearance = map(memberships, (text_score, trust, plate, visual))
    rules = [
        ("R1 exact/strong plate → strong", plate_m["high"], "strong"),
        ("R2 high text AND high visual AND high trust → strong", min(text["high"], appearance["high"], camera["high"]), "strong"),
        ("R3 high text AND medium visual → strong", min(text["high"], appearance["medium"]), "strong"),
        ("R4 medium text AND high visual AND high trust → review", min(text["medium"], appearance["high"], camera["high"]), "review"),
        ("R5 medium text AND medium visual → review", min(text["medium"], appearance["medium"]), "review"),
        ("R6 low text OR low visual → low", max(text["low"], appearance["low"]), "low"),
        ("R7 low trust AND no plate → low", min(camera["low"], plate_m["low"]), "low"),
    ]
    activations = {"low": 0.0, "review": 0.0, "strong": 0.0}
    active_rules = []
    for label, strength, output in rules:
        strength = round(strength, 4)
        activations[output] = max(activations[output], strength)
        if strength:
            active_rules.append({"rule": label, "activation": strength, "conclusion": output})

    # Aggregate clipped consequent sets across 0…100, then calculate centroid.
    universe = list(range(101))
    aggregate = [max(min(activations[name], output_membership(x)[name]) for name in activations) for x in universe]
    total = sum(aggregate)
    crisp = sum(x * membership for x, membership in zip(universe, aggregate)) / total if total else 0.0
    return {
        "inputs": {"text_similarity": memberships(text_score), "camera_trust": memberships(trust), "plate_evidence": memberships(plate), "visual_similarity": memberships(visual)},
        "rules_fired": active_rules,
        "aggregated_output": {name: round(value, 4) for name, value in activations.items()},
        "defuzzification": {"method": "centroid", "universe": "0..100", "crisp_score": round(crisp, 2)},
    }


def run(payload):
    target = payload["reference"]["vahan_match"]
    profiles = {p["camera_id"]: p for p in payload["camera_profiles"]}
    registration = payload["request"]["params"]["registration_no"].upper()
    expected = f"{target['colour']} {target['make']} {target['model']} {target['vtype']}"
    anchor = next((s for s in payload["sightings"] if any(h["plate"].upper() == registration for h in s.get("plate_hypotheses", []))), None)
    anchor_embedding = anchor.get("embedding_16", []) if anchor else []
    records = []
    for sighting in payload["sightings"]:
        # Combine structured attributes with the human-readable description.
        attributes = " ".join(str(sighting.get(key) or "") for key in ("colour", "make", "model", "vtype"))
        description = f"{attributes} {sighting.get('caption') or ''}"
        text_score, details = text_similarity(expected, description)
        plate_score = max((h["confidence"] for h in sighting.get("plate_hypotheses", []) if h["plate"].upper() == registration), default=0.0)
        visual = dot(sighting.get("embedding_16", []), anchor_embedding) if anchor_embedding else 0.0
        visual = clamp(visual)
        trust = profiles[sighting["camera_id"]]["trust_level"]
        fis = fuzzy_inference(text_score, trust, plate_score, visual)
        score = fis["defuzzification"]["crisp_score"]
        tier = "priority" if score >= 75 else "review" if score >= 40 else "low_confidence"
        records.append({
            "read_id": sighting["read_id"], "camera_id": sighting["camera_id"], "seen_at": sighting["seen_at"],
            "caption": sighting.get("caption"), "evidence": {"expected_description": expected, "text_similarity": round(text_score, 4), **details, "camera_trust": trust, "exact_plate_confidence": plate_score, "visual_similarity_to_plate_anchor": round(visual, 4)},
            "fis": fis, "fuzzy_score": score, "tier": tier,
        })
    records.sort(key=lambda row: (-row["fuzzy_score"], row["read_id"]))
    return {"_meta": {"what_this_is": "Fuzzy textual vehicle-match scores generated using a Mamdani fuzzy inference system with centroid defuzzification.", "score_range": "0 to 100"},
            "target": {"registration_no": registration, "expected_description": expected, "plate_anchor_read_id": anchor["read_id"] if anchor else None},
            "fis_design": {"inputs": ["text similarity", "camera trust", "exact plate confidence", "visual similarity"], "input_sets": ["low", "medium", "high"], "output_sets": ["low", "review", "strong"], "inference": "Mamdani max-min", "defuzzification": "centroid"},
            "sighting_scores": records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path); parser.add_argument("output", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    args.output.write_text(json.dumps(run(payload), indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
