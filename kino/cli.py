"""Command line entry point (`kino ...`)."""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
import uvicorn
from huey.consumer import Consumer
from loguru import logger
from pydantic import ValidationError
from sqlalchemy.orm import Session

from kino import __version__
from kino.admin import AdminError
from kino.config import Settings, get_settings
from kino.db.session import get_engine, get_session, init_engine, run_migrations
from kino.logging import setup_logging
from kino.models.download import ModelError, ensure_model
from kino.models.registry import MODELS
from kino.openwa.client import mask_path

app = typer.Typer(help="Kino: find your kids in WhatsApp group photos.", no_args_is_help=True)
models_app = typer.Typer(help="Model files.", no_args_is_help=True)
app.add_typer(models_app, name="models")


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
    from kino import tasks

    logger.info("starting main queue consumer with {} process worker(s)", n)
    Consumer(tasks.huey, workers=n, worker_type="process").run()


@app.command("forward-worker")
@command
def forward_worker() -> None:
    """Run the forwarding worker (forward queue only; one instance at a time)."""
    open_db()
    get_engine().dispose()
    from kino import forward

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
    from kino.admin import find_group, get_or_create_kid, map_kids_to_group

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
