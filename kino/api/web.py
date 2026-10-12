"""Serve the built React SPA (``KINO_WEB_DIR``) from the API process.

The SPA is wired in as the app's 404 handler rather than a catch-all route, so it only ever
answers requests no API route matched, and every existing status (405 on a known path with
the wrong method, JSON 404 under ``/api``) stays exactly as it was. A GET/HEAD for a path
outside the reserved API prefixes returns the file it names, or ``index.html`` for
client-side routes such as ``/kids/3/enroll``.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, Response
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException

from kino.services.files import safe_join

# Paths that belong to the API and must never fall back to the SPA.
_RESERVED_PREFIXES = ("/api/",)
_RESERVED_PATHS = frozenset({"/api", "/healthz", "/readyz", "/metrics"})

# Vite fingerprints everything under assets/ (content hash in the name): cache it forever.
_ASSETS_PREFIX = "assets/"
_IMMUTABLE = "public, max-age=31536000, immutable"
# index.html, theme-init.js, favicon.svg keep their names across releases: always revalidate.
_REVALIDATE = "no-cache"

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    ),
}


def _is_reserved(path: str) -> bool:
    return path in _RESERVED_PATHS or path.startswith(_RESERVED_PREFIXES)


def _resolve(web_dir: Path, path: str) -> Path | None:
    """The file to serve for ``path``: itself if it exists, else ``index.html`` for routes.

    None for traversal attempts and for missing files under ``assets/`` (a stale hashed
    asset must 404, not come back as HTML).
    """
    rel = path.lstrip("/")
    if not rel:
        return web_dir / "index.html"
    try:
        p = safe_join(web_dir, rel)
    except ValueError:
        return None
    if p.is_file():
        return p
    if rel.startswith(_ASSETS_PREFIX):
        return None
    return web_dir / "index.html"


def _file_response(web_dir: Path, file: Path) -> Response:
    rel = file.relative_to(web_dir).as_posix()
    cache = _IMMUTABLE if rel.startswith(_ASSETS_PREFIX) else _REVALIDATE
    return FileResponse(file, headers={"Cache-Control": cache, **SECURITY_HEADERS})


def install_spa(app: FastAPI, web_dir: Path) -> bool:
    """Serve the SPA from ``web_dir`` for unmatched GET/HEAD requests; False if it is absent."""
    try:
        root = web_dir.resolve()
    except (OSError, RuntimeError):
        root = web_dir
    if not (root / "index.html").is_file():
        logger.info("web UI not found at {}; serving the API only", web_dir)
        return False

    async def not_found(request: Request, exc: Exception) -> Response:
        assert isinstance(exc, StarletteHTTPException)
        path = request.url.path
        if exc.status_code == 404 and request.method in ("GET", "HEAD") and not _is_reserved(path):
            file = _resolve(root, path)
            if file is not None and file.is_file():
                return _file_response(root, file)
        return await http_exception_handler(request, exc)

    app.add_exception_handler(StarletteHTTPException, not_found)
    logger.info("serving web UI from {}", root)
    return True


__all__ = ["SECURITY_HEADERS", "install_spa"]
