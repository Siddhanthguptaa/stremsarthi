from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts import Category, Config, MaskStyle, load_config

PROFILES = Path(__file__).resolve().parents[2] / "config" / "profiles"


def make(**kw):
    return Config(_env_file=None, **kw)


def test_defaults_are_dev_and_safe():
    c = make()
    assert c.profile == "dev" and c.delay_seconds == 4.0 and c.gpu.enabled is False
    assert c.categories[Category.AADHAAR].style is MaskStyle.SOLID
    assert c.categories[Category.FACE].style is MaskStyle.BLUR
    assert all(cfg.mask_when_unsure for cfg in c.categories.values())
    assert c.delay_warning() is None


def test_delay_range_and_warning():
    with pytest.raises(ValidationError):
        make(delay_seconds=2.9)
    with pytest.raises(ValidationError):
        make(delay_seconds=5.1)
    assert "5.0 s" in make(delay_seconds=4.6).delay_warning()


def test_dev_gpu_cap_enforced_demo_allows_more():
    with pytest.raises(ValidationError):
        make(gpu={"enabled": True, "mem_limit_mb": 5000, "ort_provider": "cuda"})
    c = make(profile="demo", gpu={"enabled": True, "mem_limit_mb": 7000, "ort_provider": "cuda"})
    assert c.gpu.mem_limit_mb == 7000
    with pytest.raises(ValidationError):
        make(profile="demo", gpu={"enabled": True, "mem_limit_mb": 7500})


def test_cuda_provider_needs_gpu_enabled_and_dev_blocks_local_captions():
    with pytest.raises(ValidationError):
        make(gpu={"enabled": False, "ort_provider": "cuda"})
    with pytest.raises(ValidationError):
        make(captions={"provider": "indictrans2"})


def test_retro_hold_is_clamped_to_delay_minus_half_second():
    assert make().effective_retro_seconds == 3.0
    assert make(delay_seconds=3.2).effective_retro_seconds == pytest.approx(2.7)
    assert make(delay_seconds=3.0, hold={"retro_seconds": 0.0}).effective_retro_seconds == 0.0


def test_secrets_never_printed(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_super_secret_value")
    monkeypatch.setenv("YOUTUBE_STREAM_KEY", "abcd-efgh-ijkl")
    c = make()
    assert c.groq_api_key.get_secret_value() == "gsk_super_secret_value"
    blob = repr(c) + str(c) + c.model_dump_json()
    assert "gsk_super_secret_value" not in blob and "abcd-efgh-ijkl" not in blob


def test_env_alias_for_delay(monkeypatch):
    monkeypatch.setenv("STREAM_DELAY_SECONDS", "3.5")
    assert make().delay_seconds == 3.5


def test_unknown_nested_keys_rejected():
    with pytest.raises(ValidationError):
        make(detect={"stride": 3, "bogus": 1})


def test_private_terms_not_in_repr():
    c = make(chat={"streamer_private_terms": ["my street name"]})
    assert "my street name" not in repr(c)


def test_load_dev_and_demo_profiles(monkeypatch):
    monkeypatch.delenv("STREAM_DELAY_SECONDS", raising=False)
    dev = load_config("dev", config_dir=PROFILES, env_file=None)
    assert dev.profile == "dev" and dev.detect.stride == 3 and dev.gpu.enabled is False
    demo = load_config("demo", config_dir=PROFILES, env_file=None)
    assert demo.profile == "demo" and demo.detect.stride == 2 and demo.gpu.ort_provider == "cuda"
    assert demo.gpu.mem_limit_mb == 7000 and demo.captions.provider == "indictrans2"


def test_env_beats_yaml(monkeypatch):
    monkeypatch.setenv("STREAM_DELAY_SECONDS", "3.2")
    assert load_config("dev", config_dir=PROFILES, env_file=None).delay_seconds == 3.2


def test_load_rejects_bad_profile_and_missing_file(tmp_path):
    with pytest.raises(ValueError):
        load_config("prod", config_dir=PROFILES, env_file=None)
    with pytest.raises(FileNotFoundError):
        load_config("dev", config_dir=tmp_path, env_file=None)


def test_keys_proposed_by_module_specs_exist_with_spec_defaults():
    c = make()
    assert (c.engine.window.before, c.engine.window.after) == (6, 30)
    assert (c.engine.lane_max_age.fast, c.engine.lane_max_age.accurate, c.engine.lane_max_age.slow) == (
        6,
        12,
        60,
    )
    assert c.engine.protect_fill_s == 1.0 and c.engine.max_mask_area_ratio == 0.6
    assert c.runtime.modules == {
        "vision": "vision.host:build",
        "relay": "relay.runtime:build",
        "audio": "audio.orchestrator:build",
    }
    assert (c.relay.srt_latency_ms, c.relay.video_bitrate_kbps, c.relay.keyframe_seconds) == (120, 4500, 2.0)
    assert c.relay.encoder == "auto"
    assert (c.vision.face_match_cos, c.vision.face_vote_window, c.vision.face_vote_min) == (0.40, 7, 5)
    assert (c.ocr.rec_max_crops, c.ocr.rec_budget_ms, c.signboard.min_text_height_px) == (8, 150, 14)
    assert c.audio.stt_order == ["sarvam", "local"]  # Groq Whisper is a failover only (20 RPM)
    assert (c.audio.vad.on_threshold, c.audio.vad.off_threshold) == (0.5, 0.35)
    assert c.audio.stt.sarvam.transport == "rest"
    assert (c.audio.race_hedge_s, c.audio.watch_window_s, c.audio.llm_rpm, c.audio.groq_rpm) == (
        0.8,
        6.0,
        25,
        18,
    )
    assert c.captions.on_late == "skip"
    assert c.guard.weights == {
        "fingerprint": 1.0,
        "slur": 0.9,
        "risky_text": 0.6,
        "profanity": 0.5,
        "music": 0.35,
    }
    assert (c.guard.music_on_threshold, c.guard.music_off_threshold, c.guard.meter_hz) == (0.5, 0.3, 2.0)


def test_groq_rate_caps_stay_under_provider_limits():
    with pytest.raises(ValidationError):
        make(audio={"groq_rpm": 21})
    with pytest.raises(ValidationError):
        make(audio={"llm_rpm": 31})


def test_blocklist_not_in_repr():
    assert "badword" not in repr(make(guard={"risky_text_blocklist": ["badword"]}))
