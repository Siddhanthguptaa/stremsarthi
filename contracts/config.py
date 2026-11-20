"""Config: pydantic v2 + pydantic-settings.

Source priority (highest first): init kwargs, process env, ``.env``, ``config/profiles/<profile>.yaml``,
field defaults. Secrets only ever come from env / ``.env`` and are ``SecretStr`` (never printed).
The ``demo`` profile may turn on extras only through the YAML profile, never through code.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from .enums import Category, MaskStyle

DEV_GPU_CAP_MB = 3500
DEMO_GPU_CAP_MB = 7000
MAX_DELAY_FOR_5S_GATE = 4.3  # above this, capture->egress p95 can exceed 5.0 s (plan D-1)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CategoryConfig(_Model):
    enabled: bool = True
    style: MaskStyle = MaskStyle.BLUR
    sensitivity: float = Field(0.5, ge=0.0, le=1.0)
    mask_when_unsure: bool = True
    padding_px: int = Field(12, ge=0, le=200)


class DetectConfig(_Model):
    stride: int = Field(3, ge=1, le=10)  # detect every Nth frame on the fast lane
    fast_hz: float = Field(10.0, gt=0)
    accurate_hz: float = Field(4.0, gt=0)
    accurate_width: int = Field(960, ge=320, le=1920)


class GpuConfig(_Model):
    enabled: bool = False
    mem_limit_mb: int = Field(DEV_GPU_CAP_MB, ge=256)
    ort_provider: Literal["cpu", "cuda"] = "cpu"
    lock_path: str = "/tmp/streamsarthi-gpu.lock"


class VadConfig(_Model):
    on_threshold: float = Field(0.5, ge=0.0, le=1.0)
    off_threshold: float = Field(0.35, ge=0.0, le=1.0)
    min_speech_ms: int = Field(120, ge=20)
    min_silence_ms: int = Field(200, ge=20)


class SarvamSttConfig(_Model):
    # REST per segment by default; WebSocket only if the Phase 0 spike (T0.6) shows it is
    # faster and keeps order (Sarvam streaming returns finals only, no timestamps).
    transport: Literal["rest", "websocket"] = "rest"


class SttConfig(_Model):
    sarvam: SarvamSttConfig = SarvamSttConfig()


class AudioConfig(_Model):
    segment_max_s: float = Field(1.5, gt=0.3, le=3.0)
    overlap_s: float = Field(0.4, ge=0.0, le=1.0)
    pad_s: float = Field(0.2, ge=0.0, le=0.5)
    # Groq Whisper is a failover (20 RPM limit), so it is not in the default order.
    stt_order: list[Literal["sarvam", "groq", "local"]] = ["sarvam", "local"]
    llm_timeout_s: float = Field(0.7, gt=0)
    failsafe_mute_voiced: bool = True
    mute_mode: Literal["silence", "bleep"] = "silence"
    address_mute: bool = True
    languages: list[str] = ["hi", "en", "kn", "ta", "te", "bn"]
    local_stt_model: Literal["tiny", "base", "small"] = "base"
    local_stt_device: Literal["cpu", "cuda"] = "cpu"
    vad: VadConfig = VadConfig()
    stt: SttConfig = SttConfig()
    race_hedge_s: float = Field(0.8, ge=0.0)  # start the local STT if the API has not answered by then
    watch_window_s: float = Field(6.0, gt=0.0)  # rolling transcript window for the PII rules
    llm_rpm: int = Field(25, ge=1, le=30)  # Groq LLM requests per minute (limit 30)
    groq_rpm: int = Field(18, ge=1, le=20)  # Groq Whisper requests per minute (limit 20)


class HoldConfig(_Model):
    retro_seconds: float = Field(3.0, ge=0.0, le=4.5)
    slate_text_hi: str = "निजता सुरक्षा चालू"
    slate_text_en: str = "Privacy hold"


class YoutubeChatConfig(_Model):
    planned_stream_hours: float = Field(3.0, gt=0)
    daily_quota_units: int = Field(10_000, ge=1000)
    action_budget_fraction: float = Field(0.2, ge=0.0, le=0.5)
    min_poll_s: float = Field(2.0, ge=0.5)
    allow_timeout: bool = False  # a ban costs 200 quota units; deletes cost 20
    auto_delete: bool = True


class TwitchChatConfig(_Model):
    channel_login: str | None = None
    anonymous_read: bool = False  # dev without OAuth: justinfan IRC, read only, no actions
    auto_delete: bool = True
    auto_timeout: bool = True


class ClassifyConfig(_Model):
    rules_hide_threshold: float = Field(0.9, ge=0.0, le=1.0)
    allow_below: float = Field(0.3, ge=0.0, le=1.0)
    llm_enabled: bool = True
    llm_model: str = "openai/gpt-oss-20b"
    llm_batch_max: int = Field(20, ge=1, le=50)
    llm_flush_ms: int = Field(40, ge=1)
    llm_timeout_ms: int = Field(250, ge=50)
    llm_rpm: int = Field(30, ge=1, le=30)
    llm_tpm: int = Field(8000, ge=500)
    llm_pending_max_s: float = Field(10.0, gt=0)  # unserved longer than this goes to HOLD_FOR_MOD


class ChatActionsConfig(_Model):
    abuse: Literal["hide", "hold_for_mod", "delete", "timeout"] = "hide"
    severe_abuse: Literal["hide", "hold_for_mod", "delete", "timeout"] = "timeout"
    doxxing: Literal["hide", "hold_for_mod", "delete", "timeout"] = "delete"
    scam: Literal["hide", "hold_for_mod", "delete", "timeout"] = "delete"


class StreamerPrivateConfig(_Model):
    # Used for doxxing detection. In memory only, never logged, excluded from repr.
    terms: list[str] = Field(default_factory=list, repr=False)
    phones: list[str] = Field(default_factory=list, repr=False)


class ChatCommandsConfig(_Model):
    enabled: bool = True
    hold_roles: list[Literal["broadcaster", "moderator"]] = ["broadcaster", "moderator"]
    unhold_roles: list[Literal["broadcaster", "moderator"]] = ["broadcaster"]
    rate_limit_s: float = Field(2.0, ge=0.0)


class ModQueueConfig(_Model):
    max_items: int = Field(500, ge=10)
    ttl_s: int = Field(1800, ge=60)


class ChatConfig(_Model):
    enabled: bool = False
    platforms: list[Literal["youtube", "twitch"]] = ["youtube", "twitch"]
    youtube: YoutubeChatConfig = YoutubeChatConfig()
    twitch: TwitchChatConfig = TwitchChatConfig()
    classify: ClassifyConfig = ClassifyConfig()
    actions: ChatActionsConfig = ChatActionsConfig()
    streamer_private: StreamerPrivateConfig = StreamerPrivateConfig()
    commands: ChatCommandsConfig = ChatCommandsConfig()
    modqueue: ModQueueConfig = ModQueueConfig()


def _default_guard_weights() -> dict[str, float]:
    return {"fingerprint": 1.0, "slur": 0.9, "risky_text": 0.6, "profanity": 0.5, "music": 0.35}


class GuardConfig(_Model):
    enabled: bool = True
    music_on_threshold: float = Field(0.5, ge=0.0, le=1.0)
    music_off_threshold: float = Field(0.3, ge=0.0, le=1.0)
    fp_max_ber: float = Field(0.35, ge=0.0, le=1.0)  # fingerprint bit-error-rate ceiling
    fp_margin: float = Field(0.05, ge=0.0, le=1.0)  # best match must beat the runner-up by this
    profanity_enabled: bool = True
    # Words that make on-screen text risky. Matching happens in vision/ (guard never sees OCR text).
    risky_text_blocklist: list[str] = Field(default_factory=list, repr=False)
    warn_cooldown_s: float = Field(20.0, ge=0.0)
    meter_hz: float = Field(2.0, gt=0.0, le=10.0)
    weights: dict[str, float] = Field(default_factory=_default_guard_weights)  # per RiskSignal.source
    fingerprint_library_dir: str = "eval/data/generated/music_fp"


class CaptionsConfig(_Model):
    enabled: bool = False
    target_lang: str = "hi"
    provider: Literal["sarvam", "indictrans2"] = "sarvam"
    max_chars_per_line: int = Field(42, ge=10, le=80)
    # If translation is late for its display time: skip the caption or show the source text.
    on_late: Literal["skip", "show_source"] = "skip"


class Destination(_Model):
    kind: Literal["youtube", "twitch", "mediamtx_tap"]
    url_host: str | None = None  # TLS peer, e.g. a.rtmps.youtube.com
    enabled: bool = True


class WindowConfig(_Model):
    before: int = Field(6, ge=0)  # source frames before frame i whose results may cover it
    after: int = Field(30, ge=0)  # ... and after


class LaneMaxAge(_Model):
    fast: int = Field(6, ge=1)
    accurate: int = Field(12, ge=1)
    slow: int = Field(60, ge=1)


class EngineConfig(_Model):
    window: WindowConfig = WindowConfig()
    lane_max_age: LaneMaxAge = LaneMaxAge()
    protect_fill_s: float = Field(1.0, ge=0.0)  # mean-colour fill before the PROTECT slate
    max_mask_area_ratio: float = Field(0.6, gt=0.0, le=1.0)  # above this, collapse to one solid fill
    timing_log_path: str | None = None  # StageTiming JSONL (category/time only)
    cv_threads: int | None = Field(2, ge=1, le=16)  # cv2.setNumThreads cap; None leaves OpenCV's default


def _default_modules() -> dict[str, str]:
    # The engine never imports the other modules; it loads these `module:build` paths.
    return {
        "vision": "vision.host:build",
        "relay": "relay.runtime:build",
        "audio": "audio.orchestrator:build",
    }


class RuntimeConfig(_Model):
    modules: dict[str, str] = Field(default_factory=_default_modules)


class RelayConfig(_Model):
    srt_latency_ms: int = Field(120, ge=20, le=2000)  # 120 LAN, 300-500 on 4G
    video_bitrate_kbps: int = Field(4500, ge=500)
    audio_bitrate_kbps: int = Field(128, ge=32)
    keyframe_seconds: float = Field(2.0, gt=0.0, le=4.0)
    encoder: Literal["auto", "x264", "nvenc"] = "auto"
    ingest_hold_after_s: float = Field(1.0, ge=0.0)  # ingest silence before a transport HOLD
    mediamtx_api_port: int = 9997
    ingest_bind_host: str = "127.0.0.1"  # 0.0.0.0 only on purpose (LAN phone ingest) and with credentials
    rtsp_port: int = 8554  # shielded tap only; the reader itself pulls over loopback RTMP


class VisionConfig(_Model):
    allowlist_dir: str = "models/allowlist"  # enrolled embeddings (biometric): gitignored, never uploaded
    face_match_cos: float = Field(0.40, ge=0.0, le=1.0)  # UNVERIFIED default; tune on eval
    face_vote_window: int = Field(7, ge=1)
    face_vote_min: int = Field(5, ge=1)


class ApiConfig(_Model):
    host: str = "127.0.0.1"
    port: int = Field(8000, ge=1, le=65535)
    allowed_origins: list[str] = ["http://127.0.0.1:3000", "http://localhost:3000"]


class EnrolConfig(_Model):
    # Loaded by the API through the FaceEnroller protocol so app/api never imports vision.
    impl: str = "vision.detectors.faces.enroll:Enroller"
    ttl_s: int = Field(43_200, ge=60)  # enrolled embeddings expire after 12 h unless persisted
    persist: bool = False  # opt-in, encrypted at rest (biometric-derived data)
    max_faces: int = Field(8, ge=1, le=32)
    min_frames: int = Field(5, ge=1)


class EvalConfig(_Model):
    seed: int = 1234
    warmup_s: float = Field(5.0, ge=0.0)
    tap_url: str | None = None  # loopback MediaMTX `shielded` path used by the gates
    report_dir: str = "eval/reports"


class OcrConfig(_Model):
    det_model: Literal["v6_tiny", "v5_mobile"] = "v6_tiny"
    rec_max_crops: int = Field(8, ge=1)
    rec_budget_ms: int = Field(150, ge=10)


class SignboardConfig(_Model):
    min_text_height_px: int = Field(14, ge=4)


_STRONG_MASK = {
    Category.AADHAAR,
    Category.PAN,
    Category.UPI_QR,
    Category.OTP_SCREEN,
    Category.ROOM_CREDENTIAL,
    Category.GAME_ID,
}
_VIDEO_CATEGORIES = (
    Category.FACE,
    Category.AADHAAR,
    Category.PAN,
    Category.UPI_QR,
    Category.PLATE,
    Category.SIGNBOARD,
    Category.PIN_CODE,
    Category.PHONE_NUMBER,
    Category.UPI_ID,
    Category.EMAIL,
    Category.OTP_SCREEN,
    Category.GAME_ID,
    Category.ROOM_CREDENTIAL,
    Category.RISKY_TEXT,
)
_AUDIO_CATEGORIES = (
    Category.SPOKEN_PHONE,
    Category.SPOKEN_UPI,
    Category.SPOKEN_ADDRESS,
    Category.SPOKEN_OTHER_PII,
)


def default_categories() -> dict[Category, CategoryConfig]:
    out: dict[Category, CategoryConfig] = {}
    for c in _VIDEO_CATEGORIES:
        style = MaskStyle.SOLID if c in _STRONG_MASK else MaskStyle.BLUR
        # RISKY_TEXT is a strike-guard signal, off until the streamer sets a blocklist
        out[c] = CategoryConfig(style=style, enabled=c is not Category.RISKY_TEXT)
    for c in _AUDIO_CATEGORIES:
        out[c] = CategoryConfig()
    return out


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    profile: Literal["dev", "demo"] = "dev"
    delay_seconds: float = Field(
        4.0, ge=3.0, le=5.0, validation_alias=AliasChoices("delay_seconds", "stream_delay_seconds")
    )
    fps: int = Field(30, ge=15, le=60)
    canvas: tuple[int, int] = (1280, 720)

    detect: DetectConfig = DetectConfig()
    engine: EngineConfig = EngineConfig()
    runtime: RuntimeConfig = RuntimeConfig()
    relay: RelayConfig = RelayConfig()
    vision: VisionConfig = VisionConfig()
    api: ApiConfig = ApiConfig()
    enrol: EnrolConfig = EnrolConfig()
    eval: EvalConfig = EvalConfig()
    ocr: OcrConfig = OcrConfig()
    signboard: SignboardConfig = SignboardConfig()
    gpu: GpuConfig = GpuConfig()
    categories: dict[Category, CategoryConfig] = Field(default_factory=default_categories)
    audio: AudioConfig = AudioConfig()
    hold: HoldConfig = HoldConfig()
    chat: ChatConfig = ChatConfig()
    guard: GuardConfig = GuardConfig()
    captions: CaptionsConfig = CaptionsConfig()
    destinations: list[Destination] = Field(
        default_factory=lambda: [
            Destination(kind="youtube", url_host="a.rtmps.youtube.com", enabled=True),
            Destination(kind="twitch", url_host="live.twitch.tv", enabled=False),
            Destination(kind="mediamtx_tap", enabled=True),
        ]
    )

    ingest_port_srt: int = 8890
    ingest_port_rtmp: int = 1935

    # Secrets: env / .env only. Never in YAML, never printed (SecretStr).
    youtube_stream_key: SecretStr | None = None
    twitch_stream_key: SecretStr | None = None
    youtube_oauth_client_id: SecretStr | None = None
    youtube_oauth_client_secret: SecretStr | None = None
    twitch_client_id: SecretStr | None = None
    twitch_client_secret: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    sarvam_api_key: SecretStr | None = None
    ingest_publish_user: SecretStr | None = None  # MediaMTX publisher login (env INGEST_PUBLISH_USER)
    ingest_publish_password: SecretStr | None = None

    @model_validator(mode="after")
    def _profile_limits(self) -> Config:
        cap = DEV_GPU_CAP_MB if self.profile == "dev" else DEMO_GPU_CAP_MB
        if self.gpu.mem_limit_mb > cap:
            raise ValueError(f"gpu.mem_limit_mb {self.gpu.mem_limit_mb} exceeds the {self.profile} cap {cap}")
        if self.profile == "dev" and self.captions.provider == "indictrans2":
            raise ValueError("local IndicTrans2 captions are a demo-profile extra")
        if self.gpu.ort_provider == "cuda" and not self.gpu.enabled:
            raise ValueError("gpu.ort_provider=cuda requires gpu.enabled=true")
        if self.audio.overlap_s >= self.audio.segment_max_s:
            raise ValueError("audio.overlap_s must be smaller than audio.segment_max_s")
        return self

    @property
    def effective_retro_seconds(self) -> float:
        """Retroactive-hold window actually used: clamped to ``delay_seconds - 0.5``."""
        return max(0.0, min(self.hold.retro_seconds, self.delay_seconds - 0.5))

    def delay_warning(self) -> str | None:
        if self.delay_seconds > MAX_DELAY_FOR_5S_GATE:
            return (
                f"delay_seconds={self.delay_seconds} may push capture->egress p95 past 5.0 s "
                f"(ingest+encode add ~0.5 s); gate threshold is {MAX_DELAY_FOR_5S_GATE}"
            )
        return None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )


def load_config(
    profile: str | None = None,
    config_dir: str | os.PathLike[str] = "config/profiles",
    env_file: str | os.PathLike[str] | None = ".env",
) -> Config:
    """Load ``Config`` for a profile: env > ``.env`` > ``<config_dir>/<profile>.yaml`` > defaults."""
    chosen = profile or os.environ.get("STREAMSARTHI_PROFILE", "dev")
    if chosen not in ("dev", "demo"):
        raise ValueError(f"unknown profile {chosen!r} (expected 'dev' or 'demo')")
    yaml_path = Path(config_dir) / f"{chosen}.yaml"
    if not yaml_path.is_file():
        raise FileNotFoundError(f"profile file not found: {yaml_path}")

    class _Loaded(Config):
        model_config = SettingsConfigDict(yaml_file=str(yaml_path), env_file=env_file)

    return _Loaded(profile=chosen)  # type: ignore[arg-type]
