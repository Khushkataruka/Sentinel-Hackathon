"""Camera ingest.

Owns the camera connection. One task per camera, supervised, so a broken
adapter or a wedged decoder cannot take anything else down with it.
"""
