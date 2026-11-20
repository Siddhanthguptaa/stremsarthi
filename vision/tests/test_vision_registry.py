"""Registry autodiscovery, validation, duplicate ids, VRAM budget, sibling-import check (CPU only)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from contracts import Category, Config, Device, Lane
from contracts.fakes import FakeDetector, make_fake_detector
from vision import registry
from vision.registry import RegistryError, VramBudgetError

_GOOD = """
from contracts.fakes import make_fake_detector
from contracts import Lane, Category
DETECTOR = make_fake_detector({id!r}, lane=Lane.{lane}, categories=frozenset({{Category.{cat}}}))
"""


def _make_pkg(root: Path, name: str, subs: dict[str, str]) -> None:
    pkg = root / name
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for sub, body in subs.items():
        d = pkg / sub
        d.mkdir()
        (d / "__init__.py").write_text(textwrap.dedent(body))


def _cfg(**kw: object) -> Config:
    return Config(profile="dev", _env_file=None, **kw)  # type: ignore[arg-type]


def test_real_detectors_package_is_empty_or_valid() -> None:
    found = registry.discover()
    assert isinstance(found, dict)
    for ident, cls in found.items():
        assert cls.id == ident


def test_autodiscovery_finds_fake_detector_in_temp_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_pkg(
        tmp_path,
        "ssdet_ok",
        {
            "alpha": _GOOD.format(id="alpha", lane="FAST", cat="AADHAAR"),
            "beta": _GOOD.format(id="beta", lane="ACCURATE", cat="PAN"),
        },
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    found = registry.discover("ssdet_ok")
    assert sorted(found) == ["alpha", "beta"]
    assert found["alpha"].lane is Lane.FAST
    assert found["beta"].categories == frozenset({Category.PAN})
    assert issubclass(found["alpha"], FakeDetector)
    assert list(registry.discover("ssdet_ok", only=["beta"])) == ["beta"]


def test_package_without_detector_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_pkg(tmp_path, "ssdet_missing", {"nothing": "X = 1\n"})
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(RegistryError, match="does not export DETECTOR"):
        registry.discover("ssdet_missing")


def test_import_failure_is_wrapped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_pkg(tmp_path, "ssdet_boom", {"boom": "raise ValueError('x')\n"})
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(RegistryError, match="import failed"):
        registry.discover("ssdet_boom")


def test_duplicate_ids_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_pkg(
        tmp_path,
        "ssdet_dup",
        {
            "one": _GOOD.format(id="same", lane="FAST", cat="AADHAAR"),
            "two": _GOOD.format(id="same", lane="FAST", cat="PAN"),
        },
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(RegistryError, match="duplicate detector id 'same'"):
        registry.discover("ssdet_dup")


def test_only_unknown_package_name_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_pkg(tmp_path, "ssdet_only", {"a": _GOOD.format(id="a", lane="FAST", cat="PAN")})
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(RegistryError, match="not found"):
        registry.discover("ssdet_only", only=["zzz"])


def test_missing_package_yields_empty() -> None:
    assert registry.discover("ssdet_does_not_exist") == {}


@pytest.mark.parametrize(
    "attr,value",
    [
        ("id", "Bad Id"),
        ("lane", "fast"),
        ("categories", frozenset()),
        ("categories", {Category.PAN}),
        ("device", "cpu"),
        ("vram_mb", -1),
        ("vram_mb", True),
    ],
)
def test_validate_rejects_bad_declarations(attr: str, value: object) -> None:
    bad = type("Bad", (FakeDetector,), {"id": "okid", attr: value})
    with pytest.raises(RegistryError):
        registry.validate_detector_class(bad)


def test_validate_rejects_missing_method_and_non_class() -> None:
    class NoDetect:
        id = "nodetect"
        lane = Lane.FAST
        categories = frozenset({Category.PAN})
        device = Device.CPU
        vram_mb = 0

        def configure(self, cfg: object, res: object) -> None: ...
        def warmup(self) -> None: ...
        def close(self) -> None: ...

    with pytest.raises(RegistryError, match="detect"):
        registry.validate_detector_class(NoDetect)
    with pytest.raises(RegistryError, match="must be a class"):
        registry.validate_detector_class(FakeDetector())


def test_enabled_detectors_follow_categories() -> None:
    a = make_fake_detector("a", categories=frozenset({Category.PAN}))
    b = make_fake_detector("b", categories=frozenset({Category.EMAIL}))
    cfg = _cfg()
    cfg.categories[Category.EMAIL] = cfg.categories[Category.EMAIL].model_copy(update={"enabled": False})
    assert sorted(registry.enabled_detectors({"a": a, "b": b}, cfg)) == ["a"]


def _gpu_cls(ident: str, vram: int, device: Device = Device.GPU_OPTIONAL) -> type[FakeDetector]:
    return type(f"G_{ident}", (FakeDetector,), {"id": ident, "vram_mb": vram, "device": device})


def test_vram_budget_cpu_mode_counts_nothing() -> None:
    assert registry.check_vram_budget([_gpu_cls("g", 99999)], _cfg()) == 0


def test_vram_budget_gpu_mode_sums_and_refuses() -> None:
    cfg = _cfg(gpu={"enabled": True, "ort_provider": "cuda", "mem_limit_mb": 1000})
    ok = [_gpu_cls("a", 400), _gpu_cls("b", 500), _gpu_cls("cpu", 900, Device.CPU)]
    assert registry.check_vram_budget(ok, cfg) == 900
    with pytest.raises(VramBudgetError, match="1100"):
        registry.check_vram_budget([_gpu_cls("a", 600), _gpu_cls("b", 500)], cfg)
    assert registry.check_vram_budget([_gpu_cls("a", 600)], cfg, budget_mb=700) == 600
    with pytest.raises(VramBudgetError):
        registry.check_vram_budget([_gpu_cls("a", 600)], cfg, budget_mb=500)


def test_gpu_only_detector_refused_without_gpu() -> None:
    with pytest.raises(RegistryError, match="GPU-only"):
        registry.check_vram_budget([_gpu_cls("g", 100, Device.GPU_ONLY)], _cfg())


def test_cross_imports_detected(tmp_path: Path) -> None:
    root = tmp_path / "detectors"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir()
    (root / "a" / "__init__.py").write_text("from vision.detectors.b import thing\nimport vision.ocr\n")
    (root / "b" / "__init__.py").write_text("from vision.detectors.b.sub import x\n")
    problems = registry.check_no_cross_imports(root)
    assert len(problems) == 1 and "sibling detector 'b'" in problems[0]


def test_real_detector_packages_do_not_import_each_other() -> None:
    root = Path(registry.__file__).parent / "detectors"
    assert registry.check_no_cross_imports(root) == []
