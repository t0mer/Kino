"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from loguru import logger
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

import kino
from kino import metrics as kino_metrics
from kino.api.webhook import router as webhook_router
from kino.config import Settings, get_settings
from kino.db.session import get_engine, init_engine, run_migrations
from kino.openwa.client import OpenWAClient


def _make_client(settings: Settings) -> OpenWAClient | None:
    if not (settings.openwa_url and settings.openwa_api_key and settings.openwa_session_id):
        logger.warning("OpenWA URL/API key/session not fully configured; media fetch disabled")
        return None
    return OpenWAClient(
        settings.openwa_url,
        settings.openwa_api_key.get_secret_value(),
        settings.openwa_session_id,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.require_webhook_secret()  # refuse to start without it
    init_engine()
    run_migrations()
    kino_metrics.setup(settings.metrics_path)

    app = FastAPI(title="Kino", version=kino.__version__, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.openwa = _make_client(settings)
    app.include_router(webhook_router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": kino.__version__}

    @app.get("/readyz")
    def readyz() -> JSONResponse:
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ready"})

    @app.get("/metrics")
    def metrics(request: Request) -> Response:
        # Until session auth lands (M2), scraping is only open when explicitly enabled.
        if not request.app.state.settings.metrics_public:
            return Response("metrics are not public", status_code=403)
        # Merges the counters of every Kino process plus DB-derived gauges.
        return Response(
            generate_latest(kino_metrics.scrape_registry()), media_type=CONTENT_TYPE_LATEST
        )

    return app
