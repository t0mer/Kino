import os
import tempfile
import warnings

# Starlette warns once, at import, that its TestClient should use httpx2 instead of httpx.
# Import it here with that warning silenced so `pytest -W error` stays clean.
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message="Using `httpx` with `starlette.testclient`")
    import fastapi.testclient  # noqa: F401

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
