# tools

`local_grid.sh` — a local RTSP server serving looping H.264 and H.265 clips.
Everything in the README's "What was measured against a live feed" section
was produced against it, and it is the way to exercise the decode path
without access to the sandbox.

`make_test_video.py` — generates those clips. Traffic is dense at the loop
boundary on purpose: an earlier version ended on empty road, which made the
loop point look detectable when it is not.

Neither is imported by the platform.
