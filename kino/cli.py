"""Command line entry point (`kino ...`)."""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer
import uvicorn
from huey.consumer import Consumer
from loguru import logger
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kino import __version__
from kino.admin import (
    AdminError,
    find_group,
    group_kid_names,
    map_kids_to_group,
    require_group,
    require_kid,
)
from kino.config import Settings, get_settings
from kino.db.models import Group, Kid, ReferenceFace
from kino.db.session import get_engine, get_session, init_engine, run_migrations
from kino.logging import setup_logging
from kino.models.download import ModelError, ensure_model
from kino.models.registry import MODELS
from kino.openwa.client import OpenWAClient, mask_path
from kino.services import groups as groups_svc
from kino.services import kids as kids_svc

app = typer.Typer(help="Kino: find your kids in WhatsApp group photos.", no_args_is_help=True)
models_app = typer.Typer(help="Model files.", no_args_is_help=True)
app.add_typer(models_app, name="models")
groups_app = typer.Typer(help="Discovered WhatsApp groups.", no_args_is_help=True)
app.add_typer(groups_app, name="groups")
kids_app = typer.Typer(help="Kids and their save settings.", no_args_is_help=True)
app.add_typer(kids_app, name="kids")
forward_app = typer.Typer(help="Forwarding of saved photos.", no_args_is_help=True)
app.add_typer(forward_app, name="forward")
openwa_app = typer.Typer(help="OpenWA integration.", no_args_is_help=True)
app.add_typer(openwa_app, name="openwa")


class CliError(Exception):
    """A user-facing failure: printed as one `error: ...` line, exit status 1."""


def mask_chat_id(chat_id: str) -> str:
    return mask_path(chat_id)


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _fail(message: str) -> typer.Exit:
    typer.echo(f"error: {_one_line(message)}", err=True)
    return typer.Exit(1)


def _settings() -> Settings:
    try:
        return get_settings()
    except ValidationError as e:  # never echo the offending input (may be a secret)
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()
        )
        raise CliError(f"invalid configuration: {problems}") from None


def command[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Set up logging and turn expected failures into a single actionable line."""
    from kino.forward import ForwardError
    from kino.openwa.client import OpenWAError

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            setup_logging(_settings())
            return fn(*args, **kwargs)
        except (CliError, AdminError, ModelError, ForwardError, OpenWAError, RuntimeError) as e:
            raise _fail(str(e)) from None

    return wrapper


def open_db() -> None:
    """Point the global engine at the configured database and migrate it to head."""
    _settings()
    init_engine()
    run_migrations()


@contextmanager
def db_session() -> Iterator[Session]:
    open_db()
    with get_session() as s:
        yield s


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


@app.command()
@command
def migrate() -> None:
    """Create or upgrade the database schema."""
    open_db()
    typer.echo("database is up to date")


@app.command()
@command
def api(
    host: Annotated[str, typer.Option(help="Interface to bind.")] = "0.0.0.0",
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 8080,
) -> None:
    """Run the HTTP API (uvicorn)."""
    _settings().require_webhook_secret()
    open_db()
    uvicorn.run("kino.api.app:create_app", factory=True, host=host, port=port)


@app.command()
@command
def worker(
    workers: Annotated[
        int | None,
        typer.Option("--workers", "-w", min=1, help="Worker processes (default: KINO_WORKERS)."),
    ] = None,
) -> None:
    """Run the image-processing worker (main huey queue only)."""
    n = workers or _settings().workers
    open_db()
    get_engine().dispose()  # no pooled connections may cross the fork into worker processes
    from kino import metrics, tasks

    metrics.setup(_settings().metrics_path)  # before the fork: workers inherit the mode
    logger.info("starting main queue consumer with {} process worker(s)", n)
    Consumer(tasks.huey, workers=n, worker_type="process").run()


@app.command("forward-worker")
@command
def forward_worker() -> None:
    """Run the forwarding worker (forward queue only; one instance at a time)."""
    open_db()
    get_engine().dispose()
    from kino import forward, metrics

    metrics.setup(_settings().metrics_path)
    forward.run_forward_consumer()


class EmbedderChoice(StrEnum):
    auraface = "auraface"
    buffalo_l = "buffalo_l"


def _fmt_size(n: int) -> str:
    return f"{n} B" if n < 1024 else f"{n / (1 << 20):.1f} MiB"


@models_app.command("download")
@command
def models_download(
    embedder: Annotated[
        EmbedderChoice | None, typer.Option(help="Embedder to fetch (default: KINO_EMBEDDER).")
    ] = None,
    all_models: Annotated[bool, typer.Option("--all", help="Fetch every known model.")] = False,
) -> None:
    """Download and verify the detector and embedder models."""
    from kino.pipeline.embed import MODEL_NAMES

    st = _settings()
    if all_models:
        names = ["scrfd_10g", *MODEL_NAMES.values()]
    else:
        names = ["scrfd_10g", MODEL_NAMES[embedder.value if embedder else st.embedder]]
    for name in names:
        path = ensure_model(name, st.data_dir)
        spec = MODELS[name]
        typer.echo(
            f"{name}  {_fmt_size(path.stat().st_size)}  {path}  "
            f"checksum OK (sha256 {spec.sha256[:12]})"
        )


@app.command()
@command
def enroll(
    kid_name: Annotated[str, typer.Argument(help="Kid's display name (created if missing).")],
    directory: Annotated[Path, typer.Argument(help="Folder of photos of this kid.")],
    slug: Annotated[str | None, typer.Option(help="Folder slug for a new kid.")] = None,
    birth_year: Annotated[int | None, typer.Option(help="Birth year for a new kid.")] = None,
    group: Annotated[
        list[str] | None, typer.Option("--group", help="Map the kid to this group chat id.")
    ] = None,
) -> None:
    """Enroll reference faces for a kid from a directory of photos."""
    from kino import enroll as enroll_mod
    from kino import tasks
    from kino.admin import get_or_create_kid

    if not directory.is_dir():
        raise CliError(f"{directory} is not a directory")
    if not enroll_mod.list_images(directory):
        raise CliError(f"no jpg/jpeg/png/webp/heic photos in {directory}")
    st = _settings()
    with db_session() as s:
        for chat_id in group or []:
            if find_group(s, chat_id) is None:
                raise CliError(f"unknown group {mask_chat_id(chat_id)}; see `kino groups list`")
        pipe = tasks.get_pipeline()
        kid, created = get_or_create_kid(s, kid_name, slug, birth_year)
        typer.echo(f"{'created' if created else 'using'} kid {kid.display_name} ({kid.slug})")
        results = enroll_mod.enroll_directory(s, kid, directory, pipe.detector, pipe.embedder, st)
        for chat_id in group or []:
            g = find_group(s, chat_id)
            assert g is not None
            map_kids_to_group(s, g, [kid], replace=False)
        total = enroll_mod.coverage(s, kid)
    width = max(len(r.name) for r in results)
    for r in results:
        typer.echo(f"{r.name:<{width}}  {r.status:<9}  {r.detail}")
    added = sum(r.status == "enrolled" for r in results)
    typer.echo(
        f"{added} added, {total} active reference(s); "
        f"recommended minimum is {enroll_mod.RECOMMENDED_REFS}"
    )


def _openwa_client() -> OpenWAClient:
    st = _settings()
    if not (st.openwa_url and st.openwa_api_key and st.openwa_session_id):
        raise CliError("set KINO_OPENWA_URL, KINO_OPENWA_API_KEY and KINO_OPENWA_SESSION_ID")
    return OpenWAClient(st.openwa_url, st.openwa_api_key.get_secret_value(), st.openwa_session_id)


def _fetch_groups() -> list[dict[str, Any]]:
    async def go() -> list[dict[str, Any]]:
        client = _openwa_client()
        try:
            return await groups_svc.fetch_groups(client)
        finally:
            await client.aclose()

    return asyncio.run(go())


_import_groups = groups_svc.import_groups


@groups_app.command("list")
@command
def groups_list() -> None:
    """List discovered groups."""
    with db_session() as s:
        rows = list(s.scalars(select(Group).order_by(Group.id)))
        if not rows:
            typer.echo("no groups yet; they appear with their first message or via --fetch")
        for g in rows:
            kids = ", ".join(group_kid_names(s, g.id)) or "-"
            typer.echo(
                f"{mask_chat_id(g.chat_id)}  {'watched' if g.watched else 'unwatched':<9}  "
                f"{g.name or '-'}  kids: {kids}"
            )


@groups_app.command("watch")
@command
def groups_watch(
    chat_id: Annotated[str, typer.Argument(help="Group chat id, e.g. 1203...@g.us.")],
    kid: Annotated[
        list[str] | None, typer.Option("--kid", help="Kid expected in this group (repeatable).")
    ] = None,
    fetch: Annotated[
        bool, typer.Option("--fetch", help="Import group names from OpenWA first.")
    ] = False,
) -> None:
    """Start watching a group; --kid replaces its kid mapping."""
    found = _fetch_groups() if fetch else []
    with db_session() as s:
        if fetch:
            typer.echo(f"imported {_import_groups(s, found)} new group(s) from OpenWA")
        group = require_group(s, chat_id)
        kids = [require_kid(s, name) for name in kid or []]
        groups_svc.set_watched(s, group, True)
        if kid:
            map_kids_to_group(s, group, kids, replace=True)
        mapped = ", ".join(group_kid_names(s, group.id)) or "none"
        typer.echo(f"watching {mask_chat_id(group.chat_id)} ({group.name or '-'}); kids: {mapped}")


@groups_app.command("unwatch")
@command
def groups_unwatch(chat_id: Annotated[str, typer.Argument(help="Group chat id.")]) -> None:
    """Stop watching a group."""
    with db_session() as s:
        group = require_group(s, chat_id)
        groups_svc.set_watched(s, group, False)
        typer.echo(f"no longer watching {mask_chat_id(group.chat_id)}")


@kids_app.command("list")
@command
def kids_list() -> None:
    """List kids."""
    with db_session() as s:
        rows = list(s.scalars(select(Kid).order_by(Kid.id)))
        if not rows:
            typer.echo("no kids yet; run `kino enroll <name> <dir>`")
        for k in rows:
            refs = s.scalar(
                select(func.count(ReferenceFace.id)).where(
                    ReferenceFace.kid_id == k.id, ReferenceFace.status == "active"
                )
            )
            typer.echo(
                f"{k.display_name} ({k.slug})  auto-save {'on' if k.auto_save_enabled else 'off'}"
                f"  offset {k.threshold_offset:+.2f}  references {refs}"
            )


@kids_app.command("set-auto-save")
@command
def kids_set_auto_save(
    name: Annotated[str, typer.Argument(help="Kid name or slug.")],
    on: Annotated[bool, typer.Option("--on/--off", help="Enable or disable auto-save.")],
) -> None:
    """Turn auto-save on or off for a kid (off sends every match to review)."""
    with db_session() as s:
        kid = require_kid(s, name)
        kids_svc.update_kid(s, kid.id, auto_save_enabled=on)
        typer.echo(f"{kid.display_name}: auto-save {'on' if on else 'off'}")


@kids_app.command("set-offset")
@command
def kids_set_offset(
    name: Annotated[str, typer.Argument(help="Kid name or slug.")],
    offset: Annotated[float, typer.Argument(help="Threshold offset (use `--` before negatives).")],
) -> None:
    """Shift a kid's decision thresholds (positive = stricter)."""
    kids_svc.validate_offset(offset)
    with db_session() as s:
        kid = require_kid(s, name)
        kids_svc.update_kid(s, kid.id, threshold_offset=offset)
        typer.echo(f"{kid.display_name}: threshold offset {offset:+.2f}")


@forward_app.command("set")
@command
def forward_set(
    chat_id: Annotated[str, typer.Argument(help="Destination chat id (contact or group).")],
) -> None:
    """Set the default forward destination and enable forwarding."""
    from kino.forward import set_default_destination

    with db_session() as s:
        set_default_destination(s, chat_id)
    typer.echo(f"forwarding enabled to {mask_chat_id(chat_id.strip())}")


@forward_app.command("show")
@command
def forward_show() -> None:
    """Show the forwarding configuration."""
    from kino.forward import get_forward_config

    with db_session() as s:
        cfg = get_forward_config(s)
        names = kids_svc.kid_names(s)
    typer.echo(f"enabled: {'yes' if cfg.enabled else 'no'}")
    typer.echo(f"default destination: {mask_chat_id(cfg.default_chat_id or '-')}")
    for kid_id, dest in sorted(cfg.per_kid.items()):
        typer.echo(f"  {names.get(kid_id, kid_id)} -> {mask_chat_id(dest)}")
    typer.echo(f"caption: {cfg.caption_template}")
    typer.echo(f"include review-confirmed: {'yes' if cfg.include_review_confirmed else 'no'}")


@forward_app.command("disable")
@command
def forward_disable() -> None:
    """Disable forwarding (the destination is kept)."""
    from kino.forward import disable_forwarding

    with db_session() as s:
        disable_forwarding(s)
    typer.echo("forwarding disabled")


@forward_app.command("retry")
@command
def forward_retry(forward_id: Annotated[int, typer.Argument(help="Failed forward id.")]) -> None:
    """Retry a failed forward."""
    from kino.services import ServiceError
    from kino.services import forwards as forwards_svc

    try:
        with db_session() as s:
            forwards_svc.reset_failed(s, forward_id)
    except ServiceError:
        raise CliError(f"forward {forward_id} does not exist or has not failed") from None
    forwards_svc.enqueue(forward_id)
    typer.echo(f"forward {forward_id} queued for retry")


@forward_app.command("list")
@command
def forward_list(
    status: Annotated[str | None, typer.Option(help="pending, sent, failed or cancelled.")] = None,
    limit: Annotated[int, typer.Option(min=1, help="Most recent N.")] = 50,
) -> None:
    """List forwards, newest first."""
    from kino.services import forwards as forwards_svc

    with db_session() as s:
        rows = forwards_svc.list_forwards(s, status, limit)
        if not rows:
            typer.echo("no forwards")
        for f in rows:
            err = f"  {f.last_error[:80]}" if f.last_error else ""
            typer.echo(
                f"{f.id}  {f.status:<9} -> {mask_chat_id(f.dest_chat_id)}  "
                f"attempts {f.attempts}{err}"
            )


def mask_secret(_secret: str) -> str:
    return "********"


@openwa_app.command("register-webhook")
@command
def openwa_register_webhook(
    url: Annotated[
        str, typer.Option(help="URL OpenWA calls (reachable from OpenWA).")
    ] = "http://kino-api:8080/api/webhook/openwa",
) -> None:
    """Register (or update) Kino's webhook in OpenWA."""
    from kino.openwa.register import EVENTS, register_webhook

    st = _settings()
    secret = st.require_webhook_secret()

    async def go() -> tuple[str, str]:
        client = _openwa_client()
        try:
            return await register_webhook(client, url, secret)
        finally:
            await client.aclose()

    action, hook_id = asyncio.run(go())
    typer.echo(
        f"{action} webhook {hook_id}: url {url}, events {', '.join(EVENTS)}, "
        f"session {st.openwa_session_id}, secret {mask_secret(secret)}"
    )


@app.command()
@command
def bench(
    directory: Annotated[Path, typer.Argument(help="Folder of images to time.")],
    repeat: Annotated[int, typer.Option(min=1, help="Runs per image.")] = 1,
) -> None:
    """Time detect / quality / embed on real images (no database writes)."""
    from kino import bench as bench_mod
    from kino import enroll as enroll_mod
    from kino import tasks

    if not directory.is_dir():
        raise CliError(f"{directory} is not a directory")
    images = enroll_mod.list_images(directory)
    if not images:
        raise CliError(f"no jpg/jpeg/png/webp/heic images in {directory}")
    pipe = tasks.get_pipeline()
    res = bench_mod.run_bench(images, pipe.detector, pipe.embedder, _settings(), repeat)
    if not res.totals:
        raise CliError("no image could be benchmarked (all skipped by the prefilter)")
    typer.echo(f"{len(res.totals)} run(s), {res.faces} face(s); times in seconds")
    typer.echo(f"{'stage':<9} {'mean':>8} {'p50':>8} {'p95':>8}")
    for name, values in {**res.stages, "total": res.totals}.items():
        mean, p50, p95 = bench_mod.stats(values)
        typer.echo(f"{name:<9} {mean:>8.3f} {p50:>8.3f} {p95:>8.3f}")
    mean_total = bench_mod.stats(res.totals)[0]
    verdict = "within" if mean_total <= bench_mod.TARGET_SECONDS else "OVER"
    typer.echo(
        f"mean total per image {mean_total:.3f}s: {verdict} {bench_mod.TARGET_SECONDS}s target"
    )
    if res.skipped:
        typer.echo(f"skipped by prefilter: {', '.join(res.skipped)}")
