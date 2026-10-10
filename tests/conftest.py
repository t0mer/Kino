import os
import tempfile

# kino.tasks builds its huey queue file at import time; keep it out of /data.
os.environ.setdefault("KINO_DATA_DIR", tempfile.mkdtemp(prefix="kino-test-"))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _in_process_metrics():
    """create_app() switches metrics to a per-test multiprocess dir; undo it afterwards."""
    yield
    from kino import metrics

    if metrics.multiprocess_dir() is not None:
        metrics.reset()
