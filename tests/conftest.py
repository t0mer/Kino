import os
import tempfile

# kino.tasks builds its huey queue file at import time; keep it out of /data.
os.environ.setdefault("KINO_DATA_DIR", tempfile.mkdtemp(prefix="kino-test-"))
