"""Model wrappers.

Every one of these is a Protocol plus a real ONNX implementation plus a stub.
The stub is deterministic -- seeded from the crop's own pixels -- so a test
run twice produces the same answer, and so nothing downstream can accidentally
depend on randomness.

The stubs are honest about being stubs: they log once at load, and they mark
their output so it is distinguishable in the database. A stub that quietly
produced plausible-looking descriptions would be the single most dangerous
thing in this repository.
"""
