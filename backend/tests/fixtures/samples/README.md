# Sample footage

Drop real clips here, named `team-player-tier.ext`, to exercise the render pipeline
against actual footage instead of synthetic `testsrc2` patterns.

These files are gitignored. `backend/tests/integration/test_samples.py` skips cleanly
when this directory holds no media.

Include at least one clip shorter than the brand sweep (under ~1.5s) to exercise the
bare-cut path alongside the swept one.
