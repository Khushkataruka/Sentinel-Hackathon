"""Matching, route building and scoring.

Built on the same design ideas as the existing evidence engine: typed records
that always carry their source, deterministic scoring, append-only audit,
replayable. The code is new; the design is reused.
"""
