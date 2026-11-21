/**
 * State of the live risk meter (strike guard). Fed by `guard.meter` (the aggregated level), `guard.risk`
 * (single signals that live for `ttl_s`) and, after a reconnect, `GET /api/risk`.
 *
 * Privacy: a signal keeps its source, category and score only. The free-form `label` (a fingerprint id
 * or matched term) is dropped on arrival.
 */

import type { RiskSnapshot } from "@/lib/api-streamer"
import type { I18nKey } from "@/lib/i18n"
import type { Category, RiskLevel, RiskMeter, RiskSignal, RiskSource } from "@/lib/wire"

export const RISK_LEVELS: readonly RiskLevel[] = ["green", "yellow", "orange", "red"]
/** A meter reading older than this is not trusted any more: the level falls back to the live signals. */
export const METER_STALE_MS = 30_000
export const MAX_SIGNALS = 8

export interface ActiveSignal {
  key: string
  source: RiskSource
  category: Category
  score: number
  expiresAt: number
}

export interface RiskState {
  meter: { level: RiskLevel; score: number; topSources: string[]; at: number } | null
  signals: ActiveSignal[]
}

export const initialRisk: RiskState = { meter: null, signals: [] }

export type RiskEvent =
  | { type: "meter"; meter: RiskMeter; at: number }
  | { type: "signal"; signal: RiskSignal; at: number }
  | { type: "snapshot"; snapshot: RiskSnapshot; at: number }
  | { type: "tick"; now: number }

const API_LEVEL: Record<RiskSnapshot["level"], RiskLevel> = { ok: "green", watch: "yellow", warn: "orange", danger: "red" }

/** Level for a bare score when no meter reading is available. */
export function levelForScore(score: number): RiskLevel {
  if (score >= 0.85) return "red"
  if (score >= 0.6) return "orange"
  if (score >= 0.3) return "yellow"
  return "green"
}

const clamp01 = (value: number): number => (Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0)

function addSignal(signals: ActiveSignal[], signal: RiskSignal, at: number): ActiveSignal[] {
  const key = `${signal.source}:${signal.category}`
  const next: ActiveSignal = {
    key,
    source: signal.source,
    category: signal.category,
    score: clamp01(signal.score),
    expiresAt: at + Math.max(0, signal.ttl_s) * 1000,
  }
  return [next, ...signals.filter((s) => s.key !== key)].slice(0, MAX_SIGNALS)
}

export function riskReducer(state: RiskState, event: RiskEvent): RiskState {
  switch (event.type) {
    case "meter":
      return {
        ...state,
        meter: { level: event.meter.level, score: clamp01(event.meter.score), topSources: event.meter.top_sources.slice(0, 3), at: event.at },
      }
    case "signal":
      return { ...state, signals: addSignal(state.signals, event.signal, event.at) }
    case "snapshot": {
      const signals = event.snapshot.signals.reduce((acc, s) => addSignal(acc, s, event.at), [] as ActiveSignal[])
      return {
        signals,
        meter: { level: API_LEVEL[event.snapshot.level] ?? "green", score: clamp01(event.snapshot.score), topSources: [], at: event.at },
      }
    }
    case "tick": {
      const signals = state.signals.filter((s) => s.expiresAt > event.now)
      return signals.length === state.signals.length ? state : { ...state, signals }
    }
  }
}

export interface RiskView {
  level: RiskLevel
  /** 0..1 */
  score: number
  signals: ActiveSignal[]
}

/** What to show now: the fresh meter reading if there is one, otherwise the strongest live signal. */
export function riskView(state: RiskState, now: number): RiskView {
  const live = state.signals.filter((s) => s.expiresAt > now)
  const top = live.reduce((max, s) => Math.max(max, s.score), 0)
  if (state.meter && now - state.meter.at <= METER_STALE_MS) {
    return { level: state.meter.level, score: state.meter.score, signals: live }
  }
  return { level: levelForScore(top), score: top, signals: live }
}

export const RISK_LEVEL_KEY: Record<RiskLevel, I18nKey> = {
  green: "risk.level.green",
  yellow: "risk.level.yellow",
  orange: "risk.level.orange",
  red: "risk.level.red",
}

export const RISK_SOURCE_KEY: Record<RiskSource, I18nKey> = {
  music: "risk.source.music",
  fingerprint: "risk.source.fingerprint",
  profanity: "risk.source.profanity",
  risky_text: "risk.source.risky_text",
  slur: "risk.source.slur",
}
