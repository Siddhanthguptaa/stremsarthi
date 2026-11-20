/**
 * Typed mirror of the `contracts` wire format (contracts/wire.py, contract version 1.x).
 *
 * Envelope: `{"t": "<ClassName>", "v": 1, "d": {...}}`. The API relays each bus event as a WebSocket
 * frame `{"topic", "seq", "ts", "data": <envelope>}` (docs/specs/app-api.md section 5.3).
 *
 * Privacy: these types carry category, time, confidence and boxes. `ChatMessage.text`,
 * `CaptionEvent.text` and `EnrolledFaceMeta.label` are free text: render them transiently, never store them.
 *
 * Drift protection: `WIRE_FIELDS` is checked by the compiler against the interfaces below (every field
 * must be listed, none invented) and by tests/wire-drift.test.ts against the JSON samples written by
 * `python -m contracts.samples`.
 */

export const WIRE_VERSION = 1
export const CONTRACT_MAJOR = 1

// --- enums (values are wire-stable strings; tests compare them with contracts/enums.py) -------------

export const CATEGORIES = [
  "face",
  "aadhaar",
  "pan",
  "upi_qr",
  "plate",
  "signboard",
  "pin_code",
  "phone_number",
  "upi_id",
  "email",
  "otp_screen",
  "game_id",
  "room_credential",
  "risky_text",
  "spoken_phone",
  "spoken_upi",
  "spoken_address",
  "spoken_other_pii",
  "spoken_unverified",
  "chat_abuse",
  "chat_doxxing",
  "chat_scam_link",
  "chat_fake_upi",
  "music",
  "copyright_match",
  "profanity",
  "hold",
  "failsafe",
  "system_status",
] as const
export type Category = (typeof CATEGORIES)[number]

export const MODALITIES = ["video", "audio", "chat", "guard", "system"] as const
export type Modality = (typeof MODALITIES)[number]

export const MASK_STYLES = ["blur", "pixelate", "solid"] as const
export type MaskStyle = (typeof MASK_STYLES)[number]

export const MASK_KINDS = ["region", "full_frame", "mouth_of_speaker"] as const
export type MaskKind = (typeof MASK_KINDS)[number]

export const CHAT_ACTIONS = ["allow", "hide", "hold_for_mod", "delete", "timeout"] as const
export type ChatAction = (typeof CHAT_ACTIONS)[number]

export const HOLD_SOURCES = ["hotkey", "api", "chat", "app", "auto_transport", "auto_egress"] as const
export type HoldSource = (typeof HOLD_SOURCES)[number]

/** Every topic, including the engine-local ones the browser never sees. */
export const ALL_TOPICS = [
  "video.detection",
  "video.mask",
  "audio.mute",
  "audio.transcript",
  "safety.event",
  "guard.risk",
  "guard.meter",
  "caption.event",
  "chat.allowed",
  "chat.verdict",
  "chat.modqueue",
  "chat.modaction",
  "control.hold",
  "control.config",
  "control.allowlist",
  "control.session",
  "engine.status",
  "telemetry.timing",
] as const
export type Topic = (typeof ALL_TOPICS)[number]

// --- data types --------------------------------------------------------------------------------------

export type Point = [number, number]
/** Four corner points on the canonical 1280x720 canvas. */
export interface Quad {
  pts: [Point, Point, Point, Point]
}

export type SafetyAction = "masked" | "muted" | "hidden" | "held" | "deleted" | "timed_out" | "warned" | "failsafe"
export type RiskSource = "music" | "fingerprint" | "profanity" | "risky_text" | "slur"
export type RiskLevel = "green" | "yellow" | "orange" | "red"
export type Platform = "youtube" | "twitch"
export type ScalarCode = string | number | boolean

export interface Detection {
  detector: string
  category: Category
  quad: Quad
  confidence: number
  frame_seq: number
  pts: number
  completed_at: number
  track_id: number | null
  allow: boolean
  aux_quads: Record<string, Quad>
  extra: Record<string, ScalarCode>
}

export interface MaskRequest {
  request_id: string
  source: string
  category: Category
  kind: MaskKind
  quads: Quad[]
  frame_pts: number
  pts_start: number
  pts_end: number
  style: MaskStyle
  confidence: number
  reason: string
  completed_at: number
  track_id: number | null
}

export interface MuteSpan {
  span_id: string
  pts_start: number
  pts_end: number
  category: Category
  granularity: "word" | "segment" | "failsafe"
  confidence: number
  reason: string
  completed_at: number
  mode: "silence" | "bleep"
}

/** Free text: `text` and `author_display` are shown transiently and never stored. */
export interface ChatMessage {
  platform: Platform
  message_id: string
  channel_id: string
  author_id: string
  author_display: string
  text: string
  received_at: number
  is_broadcaster: boolean
  is_moderator: boolean
}

export interface ChatVerdict {
  platform: Platform
  message_id: string
  action: ChatAction
  categories: Category[]
  confidence: number
  reason: string
  stage: "rules" | "llm" | "model"
  latency_ms: number
  timeout_seconds: number | null
  platform_result: "pending" | "done" | "unsupported" | "failed"
}

/** Moderator role only: carries message text. Keep it in memory and drop it when resolved. */
export interface ModQueueItem {
  message: ChatMessage
  verdict: ChatVerdict
  enqueued_at: number
  state: "pending" | "approved" | "deleted" | "timed_out" | "dismissed"
}

export interface ModAction {
  message_id: string
  platform: Platform
  action: "approve" | "delete" | "timeout" | "dismiss"
  timeout_seconds: number | null
  actor: string
}

export interface SafetyEvent {
  event_id: string
  session_id: string
  wall_ts: string
  pts: number | null
  modality: Modality
  category: Category
  action: SafetyAction
  confidence: number
  bbox: Quad | null
  duration_ms: number | null
  source: string
  detail_code: string
}

export interface RiskSignal {
  wall_ts: string
  pts: number | null
  source: RiskSource
  category: Category
  score: number
  ttl_s: number
  label: string
}

export interface RiskMeter {
  wall_ts: string
  level: RiskLevel
  score: number
  top_sources: string[]
}

export interface HoldCommand {
  source: HoldSource
  active: boolean
  actor: string
}

export interface SessionCommand {
  action: "start" | "stop"
  actor: string
}

/** Free text: `text` is shown on the overlay only. */
export interface CaptionEvent {
  pts_start: number
  pts_end: number
  lang: string
  text: string
  is_final: boolean
}

export interface ConfigChange {
  diff: Record<string, ScalarCode | null | Array<ScalarCode | null>>
  actor: string
}

/** Local IPC only: the browser should never receive this. Present for completeness and drift checks. */
export interface AllowlistUpdate {
  op: "add" | "remove" | "snapshot"
  face_id: string | null
  label: string | null
  embedding: number[] | null
  version: number
}

export interface EnrolledFaceMeta {
  face_id: string
  label: string
  created_at: string
  expires_at: string | null
}

export type EngineState = "starting" | "ready" | "waiting_ingest" | "live" | "stopping" | "error"

export interface EngineStatus {
  state: EngineState
  hold_active: boolean
  hold_sources: HoldSource[]
  delay_s_p50: number | null
  fps: number | null
  cpu_cores: number | null
  vram_mb: number | null
  allowlist_version: number
  lanes_stale: string[]
  protect_seconds: number
  contract_version: string
}

export interface StageTiming {
  stage: string
  seq: number
  pts: number | null
  t_start: number
  t_end: number
}

export interface WireData {
  Quad: Quad
  Detection: Detection
  MaskRequest: MaskRequest
  MuteSpan: MuteSpan
  ChatMessage: ChatMessage
  ChatVerdict: ChatVerdict
  ModQueueItem: ModQueueItem
  ModAction: ModAction
  SafetyEvent: SafetyEvent
  RiskSignal: RiskSignal
  RiskMeter: RiskMeter
  HoldCommand: HoldCommand
  SessionCommand: SessionCommand
  CaptionEvent: CaptionEvent
  ConfigChange: ConfigChange
  AllowlistUpdate: AllowlistUpdate
  EnrolledFaceMeta: EnrolledFaceMeta
  EngineStatus: EngineStatus
  StageTiming: StageTiming
}
export type WireTypeName = keyof WireData

/** One entry per interface field. The mapped type makes the compiler reject missing or extra fields. */
export const WIRE_FIELDS: { [T in WireTypeName]: { [F in keyof WireData[T]]-?: true } } = {
  Quad: { pts: true },
  Detection: {
    detector: true, category: true, quad: true, confidence: true, frame_seq: true, pts: true,
    completed_at: true, track_id: true, allow: true, aux_quads: true, extra: true,
  },
  MaskRequest: {
    request_id: true, source: true, category: true, kind: true, quads: true, frame_pts: true,
    pts_start: true, pts_end: true, style: true, confidence: true, reason: true, completed_at: true,
    track_id: true,
  },
  MuteSpan: {
    span_id: true, pts_start: true, pts_end: true, category: true, granularity: true,
    confidence: true, reason: true, completed_at: true, mode: true,
  },
  ChatMessage: {
    platform: true, message_id: true, channel_id: true, author_id: true, author_display: true,
    text: true, received_at: true, is_broadcaster: true, is_moderator: true,
  },
  ChatVerdict: {
    platform: true, message_id: true, action: true, categories: true, confidence: true, reason: true,
    stage: true, latency_ms: true, timeout_seconds: true, platform_result: true,
  },
  ModQueueItem: { message: true, verdict: true, enqueued_at: true, state: true },
  ModAction: { message_id: true, platform: true, action: true, timeout_seconds: true, actor: true },
  SafetyEvent: {
    event_id: true, session_id: true, wall_ts: true, pts: true, modality: true, category: true,
    action: true, confidence: true, bbox: true, duration_ms: true, source: true, detail_code: true,
  },
  RiskSignal: { wall_ts: true, pts: true, source: true, category: true, score: true, ttl_s: true, label: true },
  RiskMeter: { wall_ts: true, level: true, score: true, top_sources: true },
  HoldCommand: { source: true, active: true, actor: true },
  SessionCommand: { action: true, actor: true },
  CaptionEvent: { pts_start: true, pts_end: true, lang: true, text: true, is_final: true },
  ConfigChange: { diff: true, actor: true },
  AllowlistUpdate: { op: true, face_id: true, label: true, embedding: true, version: true },
  EnrolledFaceMeta: { face_id: true, label: true, created_at: true, expires_at: true },
  EngineStatus: {
    state: true, hold_active: true, hold_sources: true, delay_s_p50: true, fps: true, cpu_cores: true,
    vram_mb: true, allowlist_version: true, lanes_stale: true, protect_seconds: true,
    contract_version: true,
  },
  StageTiming: { stage: true, seq: true, pts: true, t_start: true, t_end: true },
}

export const WIRE_TYPE_NAMES = Object.keys(WIRE_FIELDS) as WireTypeName[]

// --- envelope and decoding -----------------------------------------------------------------------------

export interface Wire<N extends WireTypeName = WireTypeName> {
  t: N
  v: typeof WIRE_VERSION
  d: WireData[N]
}
export type WireMessage = { [N in WireTypeName]: Wire<N> }[WireTypeName]

export class WireError extends Error {
  constructor(message: string) {
    super(message)
    this.name = "WireError"
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

/**
 * Check an envelope's type, version and that every known field is present. Unknown extra fields are
 * ignored so an additive (minor) contract change does not break an older browser; the drift test
 * catches them in CI. Values are trusted beyond that: the API validates them against the contracts.
 */
export function decodeWire(payload: unknown): WireMessage {
  if (!isRecord(payload)) throw new WireError("wire payload must be an object")
  const { t, v, d } = payload
  if (typeof t !== "string" || !(t in WIRE_FIELDS)) throw new WireError(`unknown wire type ${String(t)}`)
  if (v !== WIRE_VERSION) throw new WireError(`unsupported wire version ${String(v)}`)
  if (!isRecord(d)) throw new WireError(`${t} data must be an object`)
  for (const field of Object.keys(WIRE_FIELDS[t as WireTypeName])) {
    if (!(field in d)) throw new WireError(`${t} is missing field ${field}`)
  }
  return payload as unknown as WireMessage
}

export function decodeAs<N extends WireTypeName>(name: N, payload: unknown): WireData[N] {
  const message = decodeWire(payload)
  if (message.t !== name) throw new WireError(`expected ${name}, got ${message.t}`)
  return message.d as WireData[N]
}

// --- topics and WebSocket frames -----------------------------------------------------------------------

/** What each browser-visible topic carries. Mirrors contracts.wire.WIRE_TOPICS (engine-local topics omitted). */
export const TOPIC_TYPES = {
  "safety.event": "SafetyEvent",
  "guard.risk": "RiskSignal",
  "guard.meter": "RiskMeter",
  "caption.event": "CaptionEvent",
  "chat.allowed": "ChatMessage",
  "chat.verdict": "ChatVerdict",
  "chat.modqueue": "ModQueueItem",
  "chat.modaction": "ModAction",
  "control.hold": "HoldCommand",
  "control.config": "ConfigChange",
  "control.allowlist": "AllowlistUpdate",
  "control.session": "SessionCommand",
  "engine.status": "EngineStatus",
  "telemetry.timing": "StageTiming",
} as const satisfies Partial<Record<Topic, WireTypeName>>
export type WireTopic = keyof typeof TOPIC_TYPES
export type TopicPayload<T extends WireTopic> = WireData[(typeof TOPIC_TYPES)[T]]

export interface WsFrame<T extends WireTopic = WireTopic> {
  topic: T
  seq: number
  ts: string
  data: Wire<(typeof TOPIC_TYPES)[T]>
  /** `chat.verdict` frames carry the API's running count of hidden messages (no text). */
  meta?: { hidden_count: number }
}
export type AnyWsFrame = { [T in WireTopic]: WsFrame<T> }[WireTopic]

/**
 * Sent right after `subscribed` for a chat topic a late joiner asked for: `count` frames of the current state
 * follow (the moderator's pending queue, the streamer's recently allowed messages). The client clears what it
 * holds for `topic` when it sees this, so a reconnect never leaves stale items (or stale text) behind.
 */
export interface ReplayMarker {
  type: "replay"
  topic: WireTopic
  count: number
  hiddenCount: number
  pendingCount: number | null
}

export type WsControl =
  | { type: "ping" }
  | { type: "subscribed"; topics: string[] }
  | { type: "error"; code: string; topics?: string[] }
  | ReplayMarker

export function isWireTopic(value: unknown): value is WireTopic {
  return typeof value === "string" && value in TOPIC_TYPES
}

/** Parse one server frame: a data frame (validated) or a control frame. Returns null for anything else. */
export function parseWsFrame(text: string): AnyWsFrame | WsControl | null {
  let raw: unknown
  try {
    raw = JSON.parse(text)
  } catch {
    return null
  }
  if (!isRecord(raw)) return null
  if (raw.type === "ping") return { type: "ping" }
  if (raw.type === "subscribed" && Array.isArray(raw.topics)) {
    return { type: "subscribed", topics: raw.topics.filter((t): t is string => typeof t === "string") }
  }
  if (raw.type === "error" && typeof raw.code === "string") {
    const topics = Array.isArray(raw.topics) ? raw.topics.filter((t): t is string => typeof t === "string") : undefined
    return { type: "error", code: raw.code, topics }
  }
  if (raw.type === "replay" && isWireTopic(raw.topic) && typeof raw.count === "number") {
    return {
      type: "replay",
      topic: raw.topic,
      count: raw.count,
      hiddenCount: typeof raw.hidden_count === "number" ? raw.hidden_count : 0,
      pendingCount: typeof raw.pending_count === "number" ? raw.pending_count : null,
    }
  }
  if (!isWireTopic(raw.topic) || typeof raw.seq !== "number" || typeof raw.ts !== "string") return null
  try {
    const data = decodeWire(raw.data)
    if (data.t !== TOPIC_TYPES[raw.topic]) return null
    const hidden = isRecord(raw.meta) ? raw.meta.hidden_count : undefined
    const meta = typeof hidden === "number" ? { hidden_count: hidden } : undefined
    return { topic: raw.topic, seq: raw.seq, ts: raw.ts, data, ...(meta ? { meta } : {}) } as AnyWsFrame
  } catch {
    return null
  }
}

/** Client to server frames. The socket is read-only apart from these. */
export type ClientFrame =
  | { type: "auth"; token: string }
  | { type: "subscribe"; topics: WireTopic[] }
  | { type: "pong" }

// --- REST shapes used by the skeleton ----------------------------------------------------------------

export interface HealthResponse {
  status: "ok"
  bus: boolean
  engine: Wire<"EngineStatus"> | null
  contract_ok: boolean
  version: string
  contract_version: string
}

/** `POST /api/mod/action`. */
export interface ModActionRequest {
  message_id: string
  platform: Platform
  action: "approve" | "delete" | "timeout" | "dismiss"
  timeout_seconds?: number
}

export interface WhoAmI {
  role: "streamer" | "moderator" | "hotkey" | "viewer-readonly"
  session_id: string
  expires_at: number
}

export interface ApiErrorBody {
  error: string
  detail: string
}
