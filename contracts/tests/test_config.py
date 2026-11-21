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
    c = make(chat={"streamer_private": {"terms": ["my street name"]}})
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


def test_chat_config_matches_the_chat_spec():
    c = make().chat
    assert (
        c.youtube.planned_stream_hours,
        c.youtube.daily_quota_units,
        c.youtube.action_budget_fraction,
    ) == (3.0, 10_000, 0.2)
    assert c.youtube.allow_timeout is False and c.twitch.anonymous_read is False
    assert (c.classify.llm_rpm, c.classify.llm_timeout_ms, c.classify.llm_pending_max_s) == (30, 250, 10.0)
    assert c.actions.severe_abuse == "timeout" and c.actions.scam == "delete"
    assert c.commands.hold_roles == ["broadcaster", "moderator"] and c.commands.unhold_roles == [
        "broadcaster"
    ]
    assert (c.modqueue.max_items, c.modqueue.ttl_s) == (500, 1800)
    assert "555" not in repr(make(chat={"streamer_private": {"phones": ["555-0100"]}}))


def test_risky_text_is_off_by_default_and_ocr_model_selectable():
    c = make()
    assert c.categories[Category.RISKY_TEXT].enabled is False
    assert all(cfg.enabled for cat, cfg in c.categories.items() if cat is not Category.RISKY_TEXT)
    assert c.ocr.det_model == "v6_tiny" and make(ocr={"det_model": "v5_mobile"}).ocr.det_model == "v5_mobile"


def test_engine_cv_threads_default_and_limits():
    assert make().engine.cv_threads == 2
    assert make(engine={"cv_threads": None}).engine.cv_threads is None
    with pytest.raises(ValidationError):
        make(engine={"cv_threads": 0})


def test_relay_ingest_settings_and_secrets(monkeypatch):
    c = make()
    assert c.relay.ingest_bind_host == "127.0.0.1" and c.relay.rtsp_port == 8554
    monkeypatch.setenv("INGEST_PUBLISH_USER", "publisher-name")
    monkeypatch.setenv("INGEST_PUBLISH_PASSWORD", "publisher-secret")
    c = make()
    assert c.ingest_publish_password.get_secret_value() == "publisher-secret"
    assert "publisher-secret" not in repr(c) + c.model_dump_json()


def test_engine_audio_watchdog_defaults_and_limits():
    c = make()
    assert (
        c.engine.audio_watchdog_s,
        c.engine.audio_grace_s,
        c.engine.audio_heartbeat_lag_s,
        c.engine.audio_vouch_margin_s,
    ) == (3.0, 2.5, 2.0, 1.0)
    assert make(engine={"audio_watchdog_s": 0}).engine.audio_watchdog_s == 0  # 0 turns it off
    with pytest.raises(ValidationError):
        make(engine={"audio_watchdog_s": -1})
    with pytest.raises(ValidationError):
        make(engine={"audio_grace_s": 61})


def test_runtime_extras_default_and_override():
    assert make().runtime.extras == {
        "guard_risk": "guard.risk.service:build",
        "captions": "audio.captions:build",
        "guard_music": "guard.music:build",
    }
    assert make(runtime={"extras": {}}).runtime.extras == {}
