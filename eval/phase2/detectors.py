"""Load whatever detectors the vision registry discovers, without ever hiding a missing one.

``vision.registry.discover()`` raises when ANY package under ``vision/detectors/`` is broken, which would take
every detector's numbers down with it. Here each package is discovered on its own (``discover(only=[name])``),
so a detector that is half-written costs only its own rows. Every expected detector id ends in exactly one state:

* ``ok``          discovered, configured and warmed up: it runs and is scored
* ``missing``     no package for it under ``vision/detectors/``
* ``broken``      the package failed to import or violates the ``Detector`` protocol
* ``unavailable`` discovered but ``configure``/``warmup`` raised (typically missing model weights)
* ``error``       set later by the runner when every ``detect`` call of the run raised

Only ``ok`` detectors produce numbers; everything else is reported as NOT RUN and can never pass a gate.
Exception text is never kept (it can contain OCR text); only the exception type name.
"""

from __future__ import annotations

import pkgutil
from dataclasses import dataclass, field
from typing import Any

from contracts import Config, DetectorConfig, Device, Lane

from .rows import EXPECTED_DETECTORS

OK, MISSING, BROKEN, UNAVAILABLE, ERROR = "ok", "missing", "broken", "unavailable", "error"


@dataclass
class DetectorStatus:
    id: str
    state: str
    detail: str = ""
    lane: str | None = None
    categories: tuple[str, ...] = ()

    @property
    def runs(self) -> bool:
        return self.state == OK

    def reason(self) -> str:
        return f"{self.state}" + (f": {self.detail}" if self.detail else "")


@dataclass
class DetectorSet:
    config: Config
    resources: Any = None
    detectors: dict[str, Any] = field(default_factory=dict)  # id -> instance (ok only)
    status: dict[str, DetectorStatus] = field(default_factory=dict)
    classes: dict[str, Any] = field(default_factory=dict)
    params: dict[str, dict[str, Any]] = field(default_factory=dict)

    def by_lane(self) -> dict[Lane, list[Any]]:
        out: dict[Lane, list[Any]] = {}
        for ident, det in self.detectors.items():
            if self.status[ident].runs:
                out.setdefault(type(det).lane, []).append(det)
        return out

    def mark_error(self, ident: str, detail: str) -> None:
        if ident in self.status:
            self.status[ident] = DetectorStatus(
                ident, ERROR, detail, self.status[ident].lane, self.status[ident].categories
            )

    def summary(self) -> dict[str, dict[str, Any]]:
        return {
            i: {"state": s.state, "detail": s.detail, "lane": s.lane, "categories": list(s.categories)}
            for i, s in sorted(self.status.items())
        }

    def close(self) -> None:
        for det in self.detectors.values():
            try:
                det.close()
            except Exception:  # noqa: BLE001 - closing must never hide the numbers
                pass
        if self.resources is not None:
            try:
                self.resources.close()
            except Exception:  # noqa: BLE001
                pass


def package_names(package: str = "vision.detectors") -> list[str]:
    import importlib

    try:
        root = importlib.import_module(package)
    except ModuleNotFoundError:
        return []
    return sorted(
        i.name for i in pkgutil.iter_modules(root.__path__) if i.ispkg and not i.name.startswith("_")
    )


def discover_each(package: str = "vision.detectors") -> tuple[dict[str, Any], dict[str, DetectorStatus]]:
    """(classes by detector id, status of every package that did not load)."""
    from vision import registry

    classes: dict[str, Any] = {}
    bad: dict[str, DetectorStatus] = {}
    for name in package_names(package):
        try:
            found = registry.discover(package, only=[name])
        except Exception as exc:  # noqa: BLE001 - RegistryError and any import-time failure
            bad[name] = DetectorStatus(name, BROKEN, type(exc).__name__)
            continue
        classes.update(found)
    return classes, bad


def load_detectors(
    config: Config,
    expected: tuple[str, ...] = EXPECTED_DETECTORS,
    params: dict[str, dict[str, Any]] | None = None,
    resources: Any = None,
    package: str = "vision.detectors",
) -> DetectorSet:
    """Discover, configure and warm up every detector. Never raises for a detector problem."""
    classes, bad = discover_each(package)
    ds = DetectorSet(config, resources, classes=classes, params=dict(params or {}))
    if resources is None:
        try:
            from vision.resources import VisionResources

            ds.resources = VisionResources(config)
        except Exception as exc:  # noqa: BLE001 - without resources nothing can be configured
            for ident in sorted(set(expected) | set(classes)):
                ds.status[ident] = DetectorStatus(ident, UNAVAILABLE, f"resources: {type(exc).__name__}")
            return ds
    for ident in sorted(set(expected) | set(classes) | set(bad)):
        cls = classes.get(ident)
        if cls is None:
            ds.status[ident] = bad.get(ident) or DetectorStatus(
                ident, MISSING, "no package under vision/detectors"
            )
            continue
        status = DetectorStatus(
            ident, OK, lane=cls.lane.value, categories=tuple(sorted(c.value for c in cls.categories))
        )
        try:
            det = cls()
            det.configure(
                DetectorConfig(
                    categories=config.categories,
                    device=Device.CPU,
                    ort_provider="cpu",
                    gpu_mem_limit_mb=0,
                    params=(params or {}).get(ident, {}),
                ),
                ds.resources,
            )
            det.warmup()
            ds.detectors[ident] = det
        except Exception as exc:  # noqa: BLE001 - one detector's failure must not stop the others
            status = DetectorStatus(
                ident,
                UNAVAILABLE,
                type(exc).__name__,
                cls.lane.value,
                tuple(sorted(c.value for c in cls.categories)),
            )
        ds.status[ident] = status
    return ds
