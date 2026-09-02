"""Shared helpers for the stub implementations."""

from __future__ import annotations

import hashlib

import numpy as np


def crop_seed(crop: np.ndarray) -> int:
    """A stable seed from the image itself.

    The same crop always produces the same stub output, so tests are
    repeatable and a rerun does not silently change a record.
    """
    small = crop[::8, ::8]
    return int.from_bytes(hashlib.sha256(small.tobytes()).digest()[:8], "big")


def rng_for(crop: np.ndarray) -> np.random.Generator:
    return np.random.default_rng(crop_seed(crop) % (2**32))


def dominant_colour_name(crop: np.ndarray) -> str:
    """A crude colour name from mean BGR.

    This is not a description model. It exists so the stub's output varies
    with the input in a way a human can sanity-check on screen, rather than
    being constant.
    """
    if crop.size == 0:
        return "unknown"
    b, g, r = (float(x) for x in crop.reshape(-1, 3).mean(axis=0))
    mx, mn = max(r, g, b), min(r, g, b)
    if mx < 60:
        return "black"
    if mn > 185 and mx - mn < 30:
        return "white"
    if mx - mn < 28:
        return "silver" if mx > 120 else "grey"
    if r == mx:
        return "orange" if g > b + 30 else "red"
    if g == mx:
        return "green"
    return "blue"
