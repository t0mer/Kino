"""Prometheus metrics (CLAUDE.md section 14).

Kino runs as several processes (API, huey worker processes, forward worker) that share the
``/data`` volume, so counters use prometheus_client's multiprocess mode:

* Every long-running process calls :func:`setup` at start with ``KINO_METRICS_DIR``
  (default ``<data_dir>/metrics``). Each process then writes its values to its own mmap
  file there, named ``<kind>_<host>-<pid>.db``. The host part keeps processes of different
  containers (which can share a pid) apart.
* ``GET /metrics`` (API) merges all files with ``MultiProcessCollector``, and adds gauges
  that are read from the database at scrape time (:class:`DbStatsCollector`):
  ``kino_queue_depth``, ``kino_review_queue_size``, ``kino_gallery_refs`` and
  ``kino_forward_queue_depth``.
* Cleanup: at setup a process deletes the files of DEAD processes of its own host
  (container). A container restart keeps its hostname, so restarts do not accumulate files.
  Files of other containers are never touched (their pids are not visible here); after
  recreating containers, stale files can be removed by deleting the directory while all
  Kino containers are stopped. Counters of a removed file drop out of the sum, which
  Prometheus treats as a counter reset.

Code must reference metrics as ``metrics.<name>`` at call time: :func:`setup` rebinds them.
"""

from __future__ import annotations

import contextlib
import os
import re
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from loguru import logger
from prometheus_client import REGISTRY, CollectorRegistry, Counter, Gauge, Histogram, values
from prometheus_client.core import GaugeMetricFamily, Metric
from prometheus_client.multiprocess import MultiProcessCollector
from prometheus_client.registry import Collector

HOST = re.sub(r"[^A-Za-z0-9-]", "-", socket.gethostname()) or "host"

webhook_requests: Counter
images: Counter
duplicates: Counter
pipeline_seconds: Histogram
faces_detected: Counter
matches: Counter
forwards: Counter
digests_sent: Counter
auth: Counter
model_info: Gauge


def _define(registry: CollectorRegistry | None) -> None:
    global webhook_requests, images, duplicates, pipeline_seconds, faces_detected, matches
    global forwards, digests_sent, auth, model_info
    r = registry
    webhook_requests = Counter(
        "kino_webhook_requests_total", "Webhook requests", ["result"], registry=r
    )
    images = Counter("kino_images_total", "Images by status", ["status"], registry=r)
    duplicates = Counter("kino_duplicates_total", "Duplicate images skipped", registry=r)
    pipeline_seconds = Histogram(
        "kino_pipeline_seconds", "Pipeline stage duration", ["stage"], registry=r
    )
    faces_detected = Counter(
        "kino_faces_detected_total", "Faces detected", ["tier", "source"], registry=r
    )
    matches = Counter("kino_matches_total", "Matches", ["kid", "decision", "method"], registry=r)
    forwards = Counter(
        "kino_forwards_total", "Forwards by status and channel", ["status", "channel"], registry=r
    )
    digests_sent = Counter(
        "kino_digests_sent_total", "Review digests sent", ["channel"], registry=r
    )
    auth = Counter("kino_auth_total", "Auth attempts", ["method", "result"], registry=r)
    model_info = Gauge(
        "kino_model_info",
        "Loaded models",
        ["name", "version", "license"],
        multiprocess_mode="max",
        registry=r,
    )


_define(REGISTRY)  # in-process until setup() (tests, one-off CLI commands)
_mp_dir: Path | None = None


def _process_identifier() -> str:
    return f"{HOST}-{os.getpid()}"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _remove_dead_files(directory: Path) -> None:
    for f in directory.glob("*.db"):
        ident = f.stem.rsplit("_", 1)[-1]
        host, _, pid = ident.rpartition("-")
        if host != HOST or not pid.isdigit() or _pid_alive(int(pid)):
            continue
        with contextlib.suppress(FileNotFoundError):
            f.unlink()


def setup(directory: Path) -> None:
    """Switch this process to multiprocess metrics stored in ``directory``."""
    global _mp_dir
    directory.mkdir(parents=True, exist_ok=True)
    _remove_dead_files(directory)
    os.environ["PROMETHEUS_MULTIPROC_DIR"] = str(directory)
    values.ValueClass = values.MultiProcessValue(_process_identifier)  # type: ignore[no-untyped-call]
    _define(None)
    _mp_dir = directory


def reset() -> None:
    """Back to in-process metrics (tests)."""
    global _mp_dir
    os.environ.pop("PROMETHEUS_MULTIPROC_DIR", None)
    values.ValueClass = values.MutexValue
    _define(None)
    _mp_dir = None


def multiprocess_dir() -> Path | None:
    return _mp_dir


def set_model_info(name: str) -> None:
    """Publish ``kino_model_info`` for a registry model (version = pinned checksum prefix)."""
    from kino.models.registry import MODELS

    spec = MODELS[name]
    model_info.labels(name=name, version=spec.sha256[:12], license=spec.license).set(1)


class DbStatsCollector(Collector):
    """Gauges computed from the database at scrape time (correct across all processes)."""

    def collect(self) -> Iterator[Metric]:
        from sqlalchemy import func, select

        from kino.db.models import DELIVERY_CHANNELS, Forward, Image, Kid, Match, ReferenceFace
        from kino.db.session import get_session

        try:
            with get_session() as s:
                queued = s.scalar(select(func.count()).where(Image.status == "queued"))
                review = s.scalar(select(func.count()).where(Match.decision == "review"))
                pending_rows = s.execute(
                    select(Forward.channel, func.count())
                    .where(Forward.status == "pending")
                    .group_by(Forward.channel)
                ).all()
                refs: list[Any] = list(
                    s.execute(
                        select(Kid.slug, ReferenceFace.source, func.count())
                        .join(Kid, Kid.id == ReferenceFace.kid_id)
                        .where(ReferenceFace.status == "active")
                        .group_by(Kid.slug, ReferenceFace.source)
                    )
                )
        except Exception as e:  # a scrape must never fail because of the DB
            logger.warning("metrics DB query failed: {}", type(e).__name__)
            return
        yield GaugeMetricFamily("kino_queue_depth", "Pending pipeline jobs", value=queued or 0)
        yield GaugeMetricFamily("kino_review_queue_size", "Pending review items", value=review or 0)
        depth = GaugeMetricFamily(
            "kino_forward_queue_depth", "Pending forwards", labels=["channel"]
        )
        by_channel = {str(c): int(n) for c, n in pending_rows}
        for channel in DELIVERY_CHANNELS:
            depth.add_metric([channel], by_channel.pop(channel, 0))
        for channel, n in sorted(by_channel.items()):
            depth.add_metric([channel], n)
        yield depth
        g = GaugeMetricFamily("kino_gallery_refs", "Reference faces", labels=["kid", "source"])
        for slug, source, n in refs:
            g.add_metric([slug, source], n)
        yield g


def scrape_registry() -> CollectorRegistry:
    """Registry for ``GET /metrics``: all processes' files plus the DB gauges."""
    registry = CollectorRegistry()
    if _mp_dir is not None:
        MultiProcessCollector(registry, path=str(_mp_dir))  # type: ignore[no-untyped-call]
    else:  # not set up (tests): this process only
        registry.register(_InProcess())
    registry.register(DbStatsCollector())
    return registry


class _InProcess(Collector):
    def collect(self) -> Iterator[Metric]:
        for c in (
            webhook_requests,
            images,
            duplicates,
            pipeline_seconds,
            faces_detected,
            matches,
            forwards,
            digests_sent,
            auth,
            model_info,
        ):
            yield from c.collect()
