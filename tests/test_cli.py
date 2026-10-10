"""CLI tests: CliRunner, temp data dir, fakes via the existing injection points."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from kino import cli
from kino.config import Settings
from kino.db import session as dbs
from kino.models.download import ModelError

runner = CliRunner()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated data dir + DB; every DB-touching command opens this database."""
    settings = Settings(
        data_dir=tmp_path,
        photos_dir=tmp_path / "photos",
        webhook_secret="s3cret-webhook-secret-value",  # type: ignore[arg-type]
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(dbs, "default_url", lambda: f"sqlite:///{tmp_path / 'kino.db'}")
    yield settings
    dbs.get_engine().dispose()


def run(*args: str, input: str | None = None):
    return runner.invoke(cli.app, list(args), input=input)


def one_line_error(result) -> str:
    lines = [ln for ln in result.stderr.splitlines() if ln.strip()]
    assert result.exit_code != 0
    assert len(lines) == 1, lines
    assert lines[0].startswith("error: ")
    return lines[0]


# --- help / version -----------------------------------------------------------------------


COMMANDS = [
    [],
    ["version"],
    ["api"],
    ["worker"],
    ["forward-worker"],
    ["migrate"],
    ["models"],
    ["models", "download"],
]


@pytest.mark.parametrize("cmd", COMMANDS, ids=lambda c: " ".join(c) or "root")
def test_help_works_for_every_command(cmd):
    r = run(*cmd, "--help")
    assert r.exit_code == 0, r.output
    assert "Usage" in r.output


def test_version():
    r = run("version")
    assert r.exit_code == 0
    assert r.output.strip()


# --- migrate / api / worker ----------------------------------------------------------------


def test_migrate_creates_schema(env):
    r = run("migrate")
    assert r.exit_code == 0, r.output
    assert (env.data_dir / "kino.db").exists()


def test_api_runs_uvicorn_factory_after_migrations(env, monkeypatch):
    calls = {}
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **kw: calls.update(args=a, kw=kw))
    r = run("api", "--host", "127.0.0.1", "--port", "9001")
    assert r.exit_code == 0, r.output
    assert calls["args"] == ("kino.api.app:create_app",)
    assert calls["kw"]["factory"] is True
    assert calls["kw"]["host"] == "127.0.0.1"
    assert calls["kw"]["port"] == 9001
    assert (env.data_dir / "kino.db").exists()


def test_api_without_webhook_secret_fails_cleanly(env, monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(data_dir=env.data_dir))
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **kw: pytest.fail("must not start"))
    msg = one_line_error(run("api"))
    assert "KINO_WEBHOOK_SECRET" in msg


def test_worker_runs_main_queue_with_process_workers(env, monkeypatch):
    from kino import tasks

    made = {}

    class FakeConsumer:
        def __init__(self, h, **kw):
            made["huey"], made["kw"] = h, kw

        def run(self):
            made["ran"] = True

    monkeypatch.setattr(cli, "Consumer", FakeConsumer)
    r = run("worker", "--workers", "3")
    assert r.exit_code == 0, r.output
    assert made["huey"] is tasks.huey
    assert made["kw"] == {"workers": 3, "worker_type": "process"}
    assert made["ran"]


def test_worker_defaults_to_settings_workers(env, monkeypatch):
    made = {}

    class FakeConsumer:
        def __init__(self, h, **kw):
            made.update(kw)

        def run(self):
            pass

    monkeypatch.setattr(cli, "Consumer", FakeConsumer)
    assert run("worker").exit_code == 0
    assert made["workers"] == env.workers


def test_forward_worker_runs_forward_consumer_only(env, monkeypatch):
    from kino import forward

    called = {}
    monkeypatch.setattr(forward, "run_forward_consumer", lambda: called.update(ok=True))
    monkeypatch.setattr(cli, "Consumer", lambda *a, **k: pytest.fail("wrong consumer"))
    r = run("forward-worker")
    assert r.exit_code == 0, r.output
    assert called == {"ok": True}


def test_forward_worker_lock_error_is_one_line(env, monkeypatch):
    from kino import forward

    def boom():
        raise forward.ForwardError("another forward consumer is already running")

    monkeypatch.setattr(forward, "run_forward_consumer", boom)
    assert "already running" in one_line_error(run("forward-worker"))


# --- models download -----------------------------------------------------------------------


def test_models_download_default_embedder(env, monkeypatch, tmp_path):
    seen = []

    def fake(name, data_dir):
        seen.append((name, data_dir))
        p = tmp_path / f"{name}.onnx"
        p.write_bytes(b"x" * 10)
        return p

    monkeypatch.setattr(cli, "ensure_model", fake)
    r = run("models", "download")
    assert r.exit_code == 0, r.output
    assert [n for n, _ in seen] == ["scrfd_10g", "auraface_glintr100"]
    assert "scrfd_10g" in r.output and "checksum OK" in r.output and "10 B" in r.output


def test_models_download_buffalo_and_all(env, monkeypatch, tmp_path):
    seen = []

    def fake(name, data_dir):
        seen.append(name)
        p = tmp_path / f"{name}.onnx"
        p.write_bytes(b"x")
        return p

    monkeypatch.setattr(cli, "ensure_model", fake)
    assert run("models", "download", "--embedder", "buffalo_l").exit_code == 0
    assert seen == ["scrfd_10g", "buffalo_l_r50"]
    seen.clear()
    assert run("models", "download", "--all").exit_code == 0
    assert seen == ["scrfd_10g", "auraface_glintr100", "buffalo_l_r50"]


def test_models_download_error_is_one_line(env, monkeypatch):
    def boom(name, data_dir):
        raise ModelError("downloading model scrfd_10g failed: ConnectError")

    monkeypatch.setattr(cli, "ensure_model", boom)
    assert "ConnectError" in one_line_error(run("models", "download"))


def test_invalid_config_is_one_line(monkeypatch):
    def boom():
        return Settings(workers=0)

    monkeypatch.setattr(cli, "get_settings", boom)
    assert "workers" in one_line_error(run("migrate"))


@pytest.fixture(autouse=True)
def _reset_logging():
    yield
    from loguru import logger

    logger.remove()
