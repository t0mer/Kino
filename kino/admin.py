"""Kid and group helpers for the CLI; the logic lives in :mod:`kino.services`."""

from __future__ import annotations

from kino.services import ServiceError as AdminError
from kino.services.groups import (
    find_group,
    group_kid_names,
    map_kids_to_group,
    require_group,
)
from kino.services.kids import find_kid, get_or_create_kid, require_kid

__all__ = [
    "AdminError",
    "find_group",
    "find_kid",
    "get_or_create_kid",
    "group_kid_names",
    "map_kids_to_group",
    "require_group",
    "require_kid",
]
