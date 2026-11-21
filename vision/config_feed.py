"""Bus feed for live category settings: ``Topic.CONFIG`` (``ConfigChange``) -> the live ``config.categories``.

``PATCH /api/config`` publishes dotted-key diffs. The vision side handles exactly these keys:

    categories.<category>.enabled          strict bool
    categories.<category>.sensitivity      number in [0, 1]
    categories.<category>.mask_when_unsure strict bool
    categories.<category>.padding_px       int in [0, 200]

(``categories.<category>.style`` belongs to the engine compositor and is ignored here; every other key belongs to
another module.) The shared ``config.categories`` dict is updated by replacing the ``CategoryConfig`` entry with a
validated copy, which is atomic for the lane threads, the planner and the trackers: they all read the same dict
on every call. A switch OFF makes the lanes stop running that category's detector and the planner drops its
tracks at once; a switch ON runs the detector from its next lane run (a detector that was never warmed up is
warmed first, in this feed's thread, so the lane never blocks on a model load).

Fail closed: an unknown category or key, a value of the wrong type or range, or a malformed diff is ignored. The
safe default is ENABLED, so nothing here can switch a category off by accident (``"false"``, ``0`` or ``None``
are not booleans and are ignored). Logs carry key names only, never values.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import Any

from contracts import Category, CategoryConfig, ConfigChange, Topic

from .bus_feed import BusFeed

log = logging.getLogger("vision.config")

HANDLED_FIELDS = ("enabled", "sensitivity", "mask_when_unsure", "padding_px")


def _valid(field: str, value: Any) -> bool:
    if field in ("enabled", "mask_when_unsure"):
        return isinstance(value, bool)
    if field == "sensitivity":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and 0.0 <= value <= 1.0
        )
    if field == "padding_px":
        return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 200
    return False


class ConfigFeed(BusFeed):
    topic = Topic.CONFIG
    name = "vision-config"

    def __init__(
        self,
        bus: Any,
        categories: dict[Category, CategoryConfig],
        *,
        before_enable: Callable[[Category], None] | None = None,
        after_disable: Callable[[Category], None] | None = None,
    ) -> None:
        super().__init__(bus, log)
        self._cats = categories
        self._before_enable = before_enable
        self._after_disable = after_disable
        self.applied = 0
        self.ignored = 0

    def handle(self, event: Any) -> None:
        self.apply(event)

    def apply(self, event: Any) -> int:
        """Apply the handled keys of one change; returns how many were applied. Never raises."""
        if not isinstance(event, ConfigChange):
            self.ignored += 1
            return 0
        n = 0
        for key, value in dict(event.diff).items():
            try:
                n += int(self._apply_key(str(key), value))
            except Exception:  # noqa: BLE001 - fail closed: keep the setting as it was
                self.ignored += 1
                log.warning("config key ignored (error): %s", str(key)[:64])
        return n

    def _apply_key(self, key: str, value: Any) -> bool:
        parts = key.split(".")
        if len(parts) != 3 or parts[0] != "categories" or parts[2] == "style":
            return False  # another module's key (or the engine's): not an error
        name, field = parts[1], parts[2]
        try:
            cat = Category(name)
        except ValueError:
            self.ignored += 1
            log.warning("config key ignored (unknown category): %s", key[:64])
            return False
        cur = self._cats.get(cat)
        if cur is None or field not in HANDLED_FIELDS or not _valid(field, value):
            self.ignored += 1
            log.warning("config key ignored (invalid): %s", key[:64])
            return False
        if getattr(cur, field) == value:
            return False
        new = cur.model_copy(update={field: float(value) if field == "sensitivity" else value})
        CategoryConfig.model_validate(new.model_dump())  # range checks of the contract model
        enabling = field == "enabled" and value is True
        if enabling and self._before_enable is not None:
            self._before_enable(cat)  # warm a never-used detector first (this thread, not a lane)
        self._cats[cat] = new
        if field == "enabled" and value is False and self._after_disable is not None:
            self._after_disable(cat)
        self.applied += 1
        log.info("config applied: %s", key)
        return True
