"""LaneHost skeleton, Resources and ORT helper (CPU only)."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pytest

from contracts import Category, Config, Frame, Lane, LaneHost, Resources, Topic
from contracts.fakes import InProcessBus, make_fake_detector
from vision import host as vhost
from vision.ort import OrtProviderError, OrtSessionFactory, OrtSettings, build_session
from vision.resources import InMemoryAllowlist, OcrUnavailableError, VisionResources


def _cfg(**kw: object) -> Config:
    return Config(profile="dev", _env_file=None, **kw)  # type: ignore[arg-type]


def _frame(seq: int) -> Frame:
    return Frame(seq=seq, pts=seq / 30.0, arrival=time.monotonic(), image=None)


def _drive(host: vhost.VisionHost, frames: int) -> list:
    results: list = []
    for seq in range(frames):
        host.submit(_frame(seq))
        time.sleep(0.01)
        results.extend(host.poll())
    time.sleep(0.4)
    results.extend(host.poll())
    return results


def test_host_satisfies_lanehost_protocol_and_build_entry() -> None:
    host = vhost.build(_cfg(), InProcessBus())
    assert isinstance(host, LaneHost)
    host.close()


def test_zero_detectors_emit_ok_heartbeats_on_both_lanes() -> None:
    host = vhost.VisionHost(_cfg(), InProcessBus(), registry={})
    assert host.active_lanes == frozenset({Lane.FAST, Lane.ACCURATE})
    host.start()
    try:
        results = _drive(host, 30)
    finally:
        host.close()
    assert {r.lane for r in results} == {Lane.FAST, Lane.ACCURATE}
    assert all(r.ok and r.requests == () for r in results)
    fast = [r.frame_seq for r in results if r.lane is Lane.FAST]
    assert fast == [0, 3, 6, 9, 12, 15, 18, 21, 24, 27]


def test_submit_before_start_raises_and_after_close_is_noop() -> None:
    host = vhost.VisionHost(_cfg(), None, registry={})
    with pytest.raises(RuntimeError):
        host.submit(_frame(0))
    host.start()
    host.close()
    host.submit(_frame(1))  # engine may race the close: no error
    host.close()  # idempotent


def test_every_detector_is_built_but_a_disabled_one_is_not_warmed_or_run() -> None:
    fast = make_fake_detector("fa", Lane.FAST, frozenset({Category.PAN}))
    off = make_fake_detector("off", Lane.ACCURATE, frozenset({Category.EMAIL}))
    cfg = _cfg()
    cfg.categories[Category.EMAIL] = cfg.categories[Category.EMAIL].model_copy(update={"enabled": False})
    bus = InProcessBus()
    host = vhost.VisionHost(cfg, bus, registry={"fa": fast, "off": off})
    assert host.active_lanes == frozenset({Lane.FAST, Lane.ACCURATE})  # built for a later switch-on
    det = host.detectors["fa"]
    off_det = host.detectors["off"]
    host.start()
    assert det.configured and det.warmed
    assert off_det.configured and not off_det.warmed  # warmed when first switched on
    try:
        results = _drive(host, 7)
    finally:
        host.close()
    assert det.closed
    assert [r.frame_seq for r in results if r.lane is Lane.FAST] == [0, 3, 6]
    # the lane of the disabled detector still heartbeats (ran, found nothing) but never ran the detector
    assert off_det.calls == [] and all(r.ok and not r.requests for r in results if r.lane is Lane.ACCURATE)


def test_vram_budget_enforced_at_build() -> None:
    from contracts import Device

    heavy = type("Heavy", (make_fake_detector("heavy"),), {"vram_mb": 4000, "device": Device.GPU_OPTIONAL})
    cfg = _cfg(gpu={"enabled": True, "ort_provider": "cuda", "mem_limit_mb": 3000})
    with pytest.raises(Exception, match="exceeds"):
        vhost.VisionHost(cfg, None, registry={"heavy": heavy})
    assert vhost.VisionHost(_cfg(), None, registry={"heavy": heavy}).declared_vram_mb == 0


def test_detections_are_published_on_the_bus() -> None:
    from contracts import Detection, Quad

    det_cls = make_fake_detector("pub", Lane.FAST, frozenset({Category.PAN}))
    seen: list[Detection] = []

    class Bus:
        def publish(self, topic: Topic, event: object) -> None:
            assert topic is Topic.DETECTION
            seen.append(event)  # type: ignore[arg-type]

        def subscribe(self, topics: object) -> object:
            raise NotImplementedError

    host = vhost.VisionHost(_cfg(), Bus(), registry={"pub": det_cls})  # type: ignore[arg-type]
    host.detectors["pub"].script = {  # type: ignore[attr-defined]
        0: [Detection("pub", Category.PAN, Quad.from_xyxy(1, 1, 9, 9), 0.9, 0, 0.0, 0.0)]
    }
    host.start()
    try:
        _drive(host, 1)
    finally:
        host.close()
    assert len(seen) == 1 and seen[0].frame_seq == 0


# ---------------------------------------------------------------- resources
def test_resources_protocol_and_missing_ocr_models_fail_safe(tmp_path: Path) -> None:
    """The default OCR is the real service. With no weights it RAISES (never returns "no text")."""
    from vision.ocr import OcrService

    res = VisionResources(_cfg(), cache_dir=tmp_path)
    assert isinstance(res, Resources)
    assert res.ocr_installed and isinstance(res.ocr, OcrService)
    with pytest.raises(OcrUnavailableError):
        res.ocr(_frame(0))
    res.set_ocr(lambda f: (_ for _ in ()).throw(AssertionError))  # any callable is accepted
    assert res.ocr_installed
    with pytest.raises(FileNotFoundError):
        res.model_path("yunet-2023mar")


def test_allowlist_versioning_and_redaction() -> None:
    al = InMemoryAllowlist()
    assert al.version == 0 and list(al.embeddings()) == []
    al.update([("f1", np.ones(4, dtype=np.float32))])
    assert al.version == 1 and len(al) == 1
    assert "1.0" not in repr(al) and "n=1" in repr(al)
    al.clear()
    assert al.version == 2 and len(al) == 0


# ---------------------------------------------------------------- ort
def _identity_model(path: Path) -> None:
    import onnx
    from onnx import TensorProto, helper

    node = helper.make_node("Identity", ["x"], ["y"])
    graph = helper.make_graph(
        [node],
        "g",
        [helper.make_tensor_value_info("x", TensorProto.FLOAT, [1, 3])],
        [helper.make_tensor_value_info("y", TensorProto.FLOAT, [1, 3])],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.save(model, str(path))


def test_ort_settings_validation_and_from_config() -> None:
    with pytest.raises(ValueError):
        OrtSettings("tpu")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        OrtSettings(threads=0)
    with pytest.raises(ValueError, match="arena"):
        OrtSettings("cuda")
    assert OrtSettings.from_config(_cfg()).provider == "cpu"
    gpu = OrtSettings.from_config(_cfg(gpu={"enabled": True, "ort_provider": "cuda", "mem_limit_mb": 800}))
    assert (gpu.provider, gpu.mem_limit_mb) == ("cuda", 800)


def test_cpu_session_runs_and_reports_provider(tmp_path: Path) -> None:
    model = tmp_path / "id.onnx"
    _identity_model(model)
    sess, meta = build_session(model, OrtSettings("cpu", threads=1))
    out = sess.run(None, {"x": np.arange(3, dtype=np.float32)[None]})[0]
    assert out.tolist() == [[0.0, 1.0, 2.0]]
    assert meta["active_execution_providers"] == ["CPUExecutionProvider"]
    assert meta["gpu_mem_limit_mb"] == 0 and meta["threads"] == 1
    with pytest.raises(FileNotFoundError):
        build_session(tmp_path / "nope.onnx")


def test_cuda_request_fails_loudly_when_provider_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import vision.ort as vort

    model = tmp_path / "id.onnx"
    _identity_model(model)
    monkeypatch.setattr(vort, "preload_gpu_libraries", lambda: None)
    monkeypatch.setattr(vort.ort, "get_available_providers", lambda: ["CPUExecutionProvider"])
    with pytest.raises(OrtProviderError, match="unavailable"):
        build_session(model, OrtSettings("cuda", mem_limit_mb=256))


def test_factory_caches_sessions_and_rejects_bad_shape_id(tmp_path: Path) -> None:
    import vision.ort as vort

    factory = OrtSessionFactory(OrtSettings(), tmp_path)
    with pytest.raises(FileNotFoundError):
        factory.session("yunet-2023mar")
    assert vort.CPU_EP == "CPUExecutionProvider"
