/**
 * The post-stream safety report as the API sends it (`GET /api/report/{session_id}`, docs/specs/app-api.md 5.9,
 * `schema_version: 1`), and the sanitiser that every byte of it passes through before it is rendered or saved.
 *
 * Privacy: the report holds category, time and confidence only. `parseReport` is a second line of defence behind the
 * API's own schema test: it rebuilds the object field by field (unknown fields are dropped, so nothing that is not
 * listed here can reach the screen or the downloaded file), keeps only short code-like strings, and drops anything
 * that looks like an encoded image or a long free-text value. Bounding boxes are validated numbers, never rendered.
 */

export const REPORT_SCHEMA_VERSION = 1

export interface ReportCategoryTotal {
  count: number
  mean_confidence: number
}

export interface ReportEvent {
  event_id: string
  /** ISO-8601 UTC. */
  wall_ts: string
  pts: number | null
  modality: string
  category: string
  action: string
  confidence: number
  /** Four corner points, or null. Kept for the download only. */
  bbox: number[][] | null
  duration_ms: number | null
  source: string
  detail_code: string
}

export interface ReportHold {
  start_ts: string
  end_ts: string | null
  sources: string[]
}

export interface ReportRiskWarning {
  ts: string
  source: string
  label: string
  score: number
}

export interface SafetyReport {
  schema_version: 1
  session_id: string
  started_at: string | null
  ended_at: string | null
  duration_s: number | null
  profile: string | null
  delay_seconds: number | null
  totals: {
    /** All events (the timeline itself may be cut at 10,000). */
    events: number
    by_category: Record<string, ReportCategoryTotal>
    by_action: Record<string, number>
    by_modality: Record<string, number>
  }
  timeline: ReportEvent[]
  holds: ReportHold[]
  failsafe: { protect_seconds: number | null; audio_failsafe_seconds: number | null }
  latency: { delay_p50_s: number | null; delay_p95_s: number | null; fps_mean: number | null }
  risk: { max_level: string | null; warnings: ReportRiskWarning[] }
  chat: {
    by_action: Record<string, number>
    by_category: Record<string, number>
    platform_actions: { done: number; failed: number; unsupported: number }
  }
  notes: string[]
}

export class ReportShapeError extends Error {
  constructor(message: string) {
    super(message)
    this.name = "ReportShapeError"
  }
}

/** Upper bounds: a report is built from at most the 5,000 event ring; refuse to render more than this. */
export const MAX_TIMELINE = 20_000
const MAX_CODE = 64
const MAX_NOTE = 160
const MAX_LIST = 500

type Obj = Record<string, unknown>
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v)

/** A fixed code such as `upi_qr`, `masked`, `fp:demo_03`. Anything else (spaces aside, long, encoded) becomes "". */
export function safeCode(value: unknown, max = MAX_CODE): string {
  if (typeof value !== "string") return ""
  const v = value.trim()
  if (v.length === 0 || v.length > max) return ""
  return /^[A-Za-z0-9_.:+\-/ ]+$/.test(v) && !looksEncoded(v) ? v : ""
}

/** Long unbroken runs of base64/hex characters (an embedded image or a secret), or a data URI. */
function looksEncoded(v: string): boolean {
  return /^data:/i.test(v) || /[A-Za-z0-9+/=_-]{41,}/.test(v)
}

/** A server note: short, plain, no control characters, no encoded blobs. Dropped when it fails. */
export function safeNote(value: unknown): string {
  if (typeof value !== "string") return ""
  const v = value.replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim()
  return v.length === 0 || v.length > MAX_NOTE || looksEncoded(v) || /<[a-z!/]/i.test(v) ? "" : v
}

/** Seconds since the epoch, milliseconds since the epoch, or an ISO string -> ISO string. Null when unusable. */
export function toIso(value: unknown): string | null {
  let ms: number
  if (typeof value === "number" && Number.isFinite(value)) ms = value < 1e11 ? value * 1000 : value
  else if (typeof value === "string" && value.length <= 40) ms = Date.parse(value)
  else return null
  if (!Number.isFinite(ms)) return null
  const d = new Date(ms)
  return Number.isNaN(d.getTime()) ? null : d.toISOString()
}

const num = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null)
const count = (value: unknown): number => {
  const n = num(value)
  return n === null ? 0 : Math.max(0, Math.round(n))
}
const unit = (value: unknown): number => Math.min(1, Math.max(0, num(value) ?? 0))

function countMap(value: unknown): Record<string, number> {
  const out: Record<string, number> = {}
  if (!isObj(value)) return out
  for (const [key, v] of Object.entries(value).slice(0, MAX_LIST)) {
    const code = safeCode(key)
    if (code) out[code] = count(v)
  }
  return out
}

function parseBbox(value: unknown): number[][] | null {
  const pts = isObj(value) ? value.pts : value
  if (!Array.isArray(pts) || pts.length !== 4) return null
  const out: number[][] = []
  for (const p of pts) {
    if (!Array.isArray(p) || p.length !== 2 || num(p[0]) === null || num(p[1]) === null) return null
    out.push([p[0] as number, p[1] as number])
  }
  return out
}

function parseEvent(raw: unknown): ReportEvent | null {
  if (!isObj(raw)) return null
  const wall = toIso(raw.wall_ts)
  const category = safeCode(raw.category)
  if (!wall || !category) return null // an event with no time or category is not an event we can show
  return {
    event_id: safeCode(raw.event_id) || "-",
    wall_ts: wall,
    pts: num(raw.pts),
    modality: safeCode(raw.modality) || "system",
    category,
    action: safeCode(raw.action) || "warned",
    confidence: unit(raw.confidence),
    bbox: parseBbox(raw.bbox),
    duration_ms: num(raw.duration_ms) === null ? null : Math.max(0, Math.round(raw.duration_ms as number)),
    source: safeCode(raw.source),
    detail_code: safeCode(raw.detail_code),
  }
}

function parseHold(raw: unknown): ReportHold | null {
  if (!isObj(raw)) return null
  const start = toIso(raw.start_ts)
  if (!start) return null
  const sources = Array.isArray(raw.sources) ? raw.sources.map((s) => safeCode(s)).filter(Boolean).slice(0, 8) : []
  return { start_ts: start, end_ts: toIso(raw.end_ts), sources }
}

function parseWarning(raw: unknown): ReportRiskWarning | null {
  if (!isObj(raw)) return null
  const ts = toIso(raw.ts)
  if (!ts) return null
  return { ts, source: safeCode(raw.source), label: safeCode(raw.label), score: unit(raw.score) }
}

function deriveTotals(timeline: readonly ReportEvent[]): SafetyReport["totals"] {
  const sums: Record<string, { n: number; total: number }> = {}
  const by_action: Record<string, number> = {}
  const by_modality: Record<string, number> = {}
  for (const e of timeline) {
    const s = (sums[e.category] ??= { n: 0, total: 0 })
    s.n += 1
    s.total += e.confidence
    by_action[e.action] = (by_action[e.action] ?? 0) + 1
    by_modality[e.modality] = (by_modality[e.modality] ?? 0) + 1
  }
  const by_category: Record<string, ReportCategoryTotal> = {}
  for (const [k, s] of Object.entries(sums)) by_category[k] = { count: s.n, mean_confidence: s.n ? s.total / s.n : 0 }
  return { events: timeline.length, by_category, by_action, by_modality }
}

function parseTotals(raw: unknown, timeline: readonly ReportEvent[]): SafetyReport["totals"] {
  if (!isObj(raw) || !isObj(raw.by_category)) return deriveTotals(timeline)
  const by_category: Record<string, ReportCategoryTotal> = {}
  for (const [key, v] of Object.entries(raw.by_category).slice(0, MAX_LIST)) {
    const code = safeCode(key)
    if (code && isObj(v)) by_category[code] = { count: count(v.count), mean_confidence: unit(v.mean_confidence) }
  }
  return {
    events: count(raw.events) || timeline.length,
    by_category,
    by_action: countMap(raw.by_action),
    by_modality: countMap(raw.by_modality),
  }
}

/**
 * Validate and rebuild an API response. Throws `ReportShapeError` when it is not a report at all (wrong shape or
 * schema version); otherwise returns a clean `SafetyReport` with bad values dropped rather than shown.
 */
export function parseReport(raw: unknown): SafetyReport {
  if (!isObj(raw)) throw new ReportShapeError("not an object")
  if (raw.schema_version !== REPORT_SCHEMA_VERSION) throw new ReportShapeError("unsupported schema_version")
  if (!Array.isArray(raw.timeline)) throw new ReportShapeError("timeline missing")
  if (raw.timeline.length > MAX_TIMELINE) throw new ReportShapeError("timeline too large")

  const timeline = raw.timeline
    .map(parseEvent)
    .filter((e): e is ReportEvent => e !== null)
    .sort((a, b) => Date.parse(a.wall_ts) - Date.parse(b.wall_ts))
  const failsafe = isObj(raw.failsafe) ? raw.failsafe : {}
  const latency = isObj(raw.latency) ? raw.latency : {}
  const risk = isObj(raw.risk) ? raw.risk : {}
  const chat = isObj(raw.chat) ? raw.chat : {}
  const pa = isObj(chat.platform_actions) ? chat.platform_actions : {}

  return {
    schema_version: 1,
    session_id: safeCode(raw.session_id) || "-",
    started_at: toIso(raw.started_at),
    ended_at: toIso(raw.ended_at),
    duration_s: num(raw.duration_s),
    profile: safeCode(raw.profile) || null,
    delay_seconds: num(raw.delay_seconds),
    totals: parseTotals(raw.totals, timeline),
    timeline,
    holds: (Array.isArray(raw.holds) ? raw.holds : [])
      .slice(0, MAX_LIST)
      .map(parseHold)
      .filter((h): h is ReportHold => h !== null),
    failsafe: { protect_seconds: num(failsafe.protect_seconds), audio_failsafe_seconds: num(failsafe.audio_failsafe_seconds) },
    latency: { delay_p50_s: num(latency.delay_p50_s), delay_p95_s: num(latency.delay_p95_s), fps_mean: num(latency.fps_mean) },
    risk: {
      max_level: safeCode(risk.max_level) || null,
      warnings: (Array.isArray(risk.warnings) ? risk.warnings : [])
        .slice(0, MAX_LIST)
        .map(parseWarning)
        .filter((w): w is ReportRiskWarning => w !== null),
    },
    chat: {
      by_action: countMap(chat.by_action),
      by_category: countMap(chat.by_category),
      platform_actions: { done: count(pa.done), failed: count(pa.failed), unsupported: count(pa.unsupported) },
    },
    notes: (Array.isArray(raw.notes) ? raw.notes : []).slice(0, 20).map(safeNote).filter(Boolean),
  }
}
