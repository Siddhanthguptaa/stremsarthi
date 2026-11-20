"""Detector plugin autodiscovery and validation.

Every ``vision/detectors/<id>/__init__.py`` exports ``DETECTOR``, a class that satisfies the
``contracts.Detector`` protocol. The registry imports each subpackage, checks the class
declaration (class variables and methods), refuses duplicate ids, and checks the declared VRAM
against the profile budget. Adding a detector therefore needs no edit to any shared file.

Frames never leave the machine, so a detector package must not import the network stack; that is
reviewed by the reviewer agent, not enforced here. What is enforced here: detector packages must not
import each other (shared helpers live in ``vision.ocr``, ``vision.patterns``, ``vision.tracking``).
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import ModuleType

from contracts import Category, Config, Detector, Device, Lane

DETECTORS_PACKAGE = "vision.detectors"
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_METHODS = ("configure", "warmup", "detect", "close")


class RegistryError(RuntimeError):
    """A detector package is missing, malformed, duplicated or over budget."""


class VramBudgetError(RegistryError):
    """The enabled detectors declare more GPU memory than the profile allows."""


def validate_detector_class(cls: object, *, source: str = "?") -> type[Detector]:
    """Check that ``cls`` declares everything the ``Detector`` protocol needs. Returns it typed."""
    if not inspect.isclass(cls):
        raise RegistryError(f"{source}: DETECTOR must be a class, got {type(cls).__name__}")
    ident = getattr(cls, "id", None)
    if not isinstance(ident, str) or not _ID_RE.match(ident):
        raise RegistryError(f"{source}: DETECTOR.id must match {_ID_RE.pattern}, got {ident!r}")
    if not isinstance(getattr(cls, "lane", None), Lane):
        raise RegistryError(f"{source}: {ident}.lane must be a contracts.Lane")
    cats = getattr(cls, "categories", None)
    if not isinstance(cats, frozenset) or not cats or not all(isinstance(c, Category) for c in cats):
        raise RegistryError(f"{source}: {ident}.categories must be a non-empty frozenset[Category]")
    if not isinstance(getattr(cls, "device", None), Device):
        raise RegistryError(f"{source}: {ident}.device must be a contracts.Device")
    vram = getattr(cls, "vram_mb", None)
    if isinstance(vram, bool) or not isinstance(vram, int) or vram < 0:
        raise RegistryError(f"{source}: {ident}.vram_mb must be a non-negative int")
    for name in _METHODS:
        if not callable(getattr(cls, name, None)):
            raise RegistryError(f"{source}: {ident} is missing method {name}()")
    return cls  # type: ignore[return-value]


def discover(
    package: str = DETECTORS_PACKAGE, *, only: Iterable[str] | None = None
) -> dict[str, type[Detector]]:
    """Import every subpackage of ``package`` and collect its ``DETECTOR``.

    Returns ``{detector_id: class}``. A subpackage that fails to import, has no ``DETECTOR``, is
    malformed, or reuses an id raises ``RegistryError`` (failing loudly beats a silently missing
    detector, which would be an unmasked category). ``only`` limits discovery to those subpackage
    names. A missing ``package`` (no detectors written yet) yields an empty registry.
    """
    try:
        root = importlib.import_module(package)
    except ModuleNotFoundError as exc:
        if exc.name == package or (exc.name and package.startswith(exc.name + ".")):
            return {}
        raise RegistryError(f"cannot import {package}: {exc}") from exc
    wanted = None if only is None else set(only)
    found: dict[str, type[Detector]] = {}
    owner: dict[str, str] = {}
    for info in sorted(pkgutil.iter_modules(root.__path__), key=lambda i: i.name):
        if not info.ispkg or info.name.startswith("_"):
            continue
        if wanted is not None and info.name not in wanted:
            continue
        modname = f"{package}.{info.name}"
        try:
            module: ModuleType = importlib.import_module(modname)
        except Exception as exc:  # noqa: BLE001 - re-raised with the package name
            raise RegistryError(f"{modname}: import failed: {type(exc).__name__}: {exc}") from exc
        if not hasattr(module, "DETECTOR"):
            raise RegistryError(f"{modname}: __init__.py does not export DETECTOR")
        cls = validate_detector_class(module.DETECTOR, source=modname)
        if cls.id in found:
            raise RegistryError(f"duplicate detector id {cls.id!r} in {owner[cls.id]} and {modname}")
        found[cls.id] = cls
        owner[cls.id] = modname
    if wanted is not None and (missing := wanted - {m.rsplit(".", 1)[-1] for m in owner.values()}):
        raise RegistryError(f"detector package(s) not found: {', '.join(sorted(missing))}")
    return found


def enabled_detectors(registry: Mapping[str, type[Detector]], config: Config) -> dict[str, type[Detector]]:
    """Detectors with at least one category enabled in ``config.categories``."""
    out: dict[str, type[Detector]] = {}
    for ident, cls in registry.items():
        if any(config.categories.get(c) is not None and config.categories[c].enabled for c in cls.categories):
            out[ident] = cls
    return out


def uses_gpu(config: Config) -> bool:
    return bool(config.gpu.enabled and config.gpu.ort_provider == "cuda")


def vram_required_mb(classes: Iterable[type[Detector]], config: Config) -> int:
    """Declared GPU memory the classes would take under ``config`` (0 on the CPU provider)."""
    if not uses_gpu(config):
        return 0
    return sum(c.vram_mb for c in classes if c.device is not Device.CPU)


def check_vram_budget(
    classes: Iterable[type[Detector]], config: Config, *, budget_mb: int | None = None
) -> int:
    """Refuse a configuration whose declared VRAM exceeds ``Config.gpu.mem_limit_mb``.

    ``budget_mb`` overrides the limit (the host can pass the vision share of the profile cap).
    GPU-only detectors cannot run when the GPU provider is off. Returns the declared total.
    """
    classes = list(classes)
    if not uses_gpu(config):
        gpu_only = [c.id for c in classes if c.device is Device.GPU_ONLY]
        if gpu_only:
            raise RegistryError(
                f"detectors {sorted(gpu_only)} are GPU-only but gpu.enabled/ort_provider is not cuda"
            )
        return 0
    limit = config.gpu.mem_limit_mb if budget_mb is None else budget_mb
    total = vram_required_mb(classes, config)
    if total > limit:
        detail = ", ".join(f"{c.id}={c.vram_mb}" for c in classes if c.device is not Device.CPU)
        raise VramBudgetError(f"declared vision VRAM {total} MB exceeds the {limit} MB budget ({detail})")
    return total


def check_no_cross_imports(detectors_dir: str | Path, package: str = DETECTORS_PACKAGE) -> list[str]:
    """Static check: no detector subpackage imports a sibling detector. Returns violation strings."""
    root = Path(detectors_dir)
    names = {p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")}
    problems: list[str] = []
    for name in sorted(names):
        for path in sorted((root / name).rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError as exc:
                problems.append(f"{path}: syntax error {exc.msg}")
                continue
            for node in ast.walk(tree):
                targets: list[str] = []
                if isinstance(node, ast.Import):
                    targets = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:  # relative import: climbing out of the package is the smell
                        if node.level >= 2:
                            targets = [("." * node.level) + (node.module or "")]
                            problems.append(f"{path}:{node.lineno}: relative import leaves detector package")
                        continue
                    base = node.module or ""
                    targets = [base] + [f"{base}.{a.name}" for a in node.names]
                for t in targets:
                    parts = t.split(".")
                    pkg_parts = package.split(".")
                    if parts[: len(pkg_parts)] == pkg_parts and len(parts) > len(pkg_parts):
                        other = parts[len(pkg_parts)]
                        if other != name and other in names:
                            problems.append(f"{path}:{node.lineno}: imports sibling detector {other!r}")
    return sorted(set(problems))
