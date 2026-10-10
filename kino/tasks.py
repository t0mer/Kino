"""Huey queue and task stubs. Task 10 fills in `process_image`."""

from __future__ import annotations

from huey import SqliteHuey

from kino.config import get_settings

_data_dir = get_settings().data_dir
_data_dir.mkdir(parents=True, exist_ok=True)

huey = SqliteHuey("kino", filename=str(_data_dir / "queue.db"))

# Forwarding has its own queue so a single consumer (`-w 1`) keeps sends ordered and rate limited.
forward_huey = SqliteHuey("kino-forward", filename=str(_data_dir / "forward_queue.db"))


@huey.task()  # type: ignore[untyped-decorator]
def process_image(image_id: int) -> None:
    """Run the face pipeline on one stored image (implemented in a later task)."""
