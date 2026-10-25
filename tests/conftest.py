import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "generator"))
sys.path.insert(0, os.path.join(ROOT, "analysis"))

START = dt.datetime(2026, 6, 10, tzinfo=dt.timezone.utc)


@pytest.fixture
def make_batch(tmp_path):
    """Generate a batch into a temp dir and return (path, manifest)."""
    import generate_batch as gb

    def _make(name="batch", days=2, seed=7, **kwargs):
        out = tmp_path / name
        manifest = gb.generate(str(out), START, days, seed, 120, 10, 0, **kwargs)
        return out, manifest

    return _make
