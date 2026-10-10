"""Download, verify and cache model files."""

import hashlib
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

import httpx
from loguru import logger

from kino.models.registry import MODELS, ModelSpec

_TIMEOUT = httpx.Timeout(30.0, read=120.0)
_MAX_REDIRECTS = 5


class ModelError(RuntimeError):
    """Raised when a model cannot be fetched or fails verification."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path) -> None:
    """Stream ``url`` to ``dest``; redirects are followed only to https."""
    with httpx.Client(timeout=_TIMEOUT, follow_redirects=False) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            with client.stream("GET", url) as resp:
                if resp.is_redirect:
                    nxt = resp.headers.get("location", "")
                    url = str(resp.url.join(nxt))
                    if not url.startswith("https://"):
                        raise ModelError("refusing redirect to non-https URL")
                    continue
                resp.raise_for_status()
                with dest.open("wb") as f:
                    for chunk in resp.iter_bytes(1 << 20):
                        f.write(chunk)
                return
    raise ModelError("too many redirects")


def _fetch(spec: ModelSpec, tmp_dir: Path) -> Path:
    """Fetch the model into ``tmp_dir`` and return the (unverified) file path."""
    fd, raw_name = tempfile.mkstemp(dir=tmp_dir, prefix=".dl-")
    os.close(fd)
    raw = Path(raw_name)
    try:
        _download(spec.url, raw)
        if spec.archive_member is None:
            return raw
        fd, out_name = tempfile.mkstemp(dir=tmp_dir, prefix=".ex-")
        os.close(fd)
        out = Path(out_name)
        try:
            with (
                zipfile.ZipFile(raw) as zf,
                zf.open(spec.archive_member) as src,
                out.open("wb") as dst,
            ):
                shutil.copyfileobj(src, dst, 1 << 20)
        except BaseException:
            out.unlink(missing_ok=True)
            raise
        raw.unlink(missing_ok=True)
        return out
    except BaseException:
        raw.unlink(missing_ok=True)
        raise


def ensure_model(name: str, data_dir: Path | str) -> Path:
    """Return the path of model ``name``, downloading and verifying it if needed."""
    try:
        spec = MODELS[name]
    except KeyError:
        raise ModelError(f"unknown model: {name}") from None
    models_dir = Path(data_dir) / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    final = models_dir / spec.filename

    if final.exists():
        if _sha256(final) == spec.sha256:
            _announce(name, spec)
            return final
        logger.warning("model {} failed checksum, re-downloading", name)
        final.unlink()

    logger.info("downloading model {} from {}", name, spec.url)
    try:
        tmp = _fetch(spec, models_dir)
    except KeyError as e:
        raise ModelError(
            f"model {name}: file {e.args[0]!r} not found in downloaded archive"
        ) from None
    except (zipfile.BadZipFile, httpx.HTTPError) as e:
        raise ModelError(f"downloading model {name} failed: {type(e).__name__}") from None
    try:
        actual = _sha256(tmp)
        if actual != spec.sha256:
            raise ModelError(f"checksum mismatch for {name}: expected {spec.sha256}, got {actual}")
        tmp.replace(final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _announce(name, spec)
    return final


def _announce(name: str, spec: ModelSpec) -> None:
    logger.info("model {} loaded (license: {})", name, spec.license)
    if name == "buffalo_l_r50":
        logger.warning("buffalo_l_r50 is licensed for non-commercial research use only")
