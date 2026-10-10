import os

# The Docker image sets KINO_VERSION to the release version at build time.
__version__ = os.environ.get("KINO_VERSION") or "0.0.0"
