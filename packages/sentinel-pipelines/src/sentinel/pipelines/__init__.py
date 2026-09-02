"""The four crop pipelines.

Each consumes one queue, writes its own columns on the sighting, sets its own
status, and posts a completion event. They never wait on each other: a
pipeline that never runs sets its status to 'skipped' at insert and never
blocks correlation.
"""
