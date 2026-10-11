"""One path-containment helper for every route that serves a file from disk (CLAUDE.md §13)."""

from __future__ import annotations

from pathlib import Path


def safe_join(base: Path, rel: str | Path) -> Path:
    """``base / rel`` fully resolved, guaranteed to lie strictly inside ``base``.

    ``rel`` may be relative or an absolute path that already points inside ``base`` (the DB
    stores absolute paths). Raises ``ValueError`` for an empty or NUL-bearing path, ``..``
    traversal, an absolute path elsewhere, or a symlink that leaves ``base``. Existence is
    not checked.
    """
    text = str(rel)
    if not text or "\0" in text:
        raise ValueError("invalid path")
    try:
        root = base.resolve()
        p = (root / text).resolve()
    except (OSError, RuntimeError, ValueError) as e:
        raise ValueError("invalid path") from e
    if p == root or not p.is_relative_to(root):
        raise ValueError("path escapes base directory")
    return p


def existing_file(base: Path, rel: str | Path | None) -> Path | None:
    """The regular file ``rel`` inside ``base``, or None if unset, unsafe or missing."""
    if not rel:
        return None
    try:
        p = safe_join(base, rel)
    except ValueError:
        return None
    return p if p.is_file() else None


__all__ = ["existing_file", "safe_join"]
