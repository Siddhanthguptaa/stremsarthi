/** Pure helpers that turn a `SafetyReport` into what the report screen draws. No DOM, no network. */

import type { ReportEvent, SafetyReport } from "@/lib/report-types"

export interface Lane {
  category: string
  count: number
  /** Positions 0..1 along the session, one per event (capped), with the event's confidence. */
  marks: { at: number; confidence: number; index: number }[]
}

export interface Span {
  startMs: number
  endMs: number
}

export interface HoldBand {
  from: number
  to: number
}

export interface TimelineModel {
  span: Span
  lanes: Lane[]
  holds: HoldBand[]
  /** True when events were left off the chart (the table still lists them). */
  capped: boolean
}

const MAX_MARKS_PER_LANE = 400

/** The session's time span: stated start and end when present, else the first and last event. */
export function reportSpan(report: SafetyReport): Span {
  const times = report.timeline.map((e) => Date.parse(e.wall_ts))
  for (const h of report.holds) {
    times.push(Date.parse(h.start_ts))
    if (h.end_ts) times.push(Date.parse(h.end_ts))
  }
  const start = report.started_at ? Date.parse(report.started_at) : times.length ? Math.min(...times) : 0
  const lastKnown = times.length ? Math.max(...times) : start
  const end = report.ended_at ? Date.parse(report.ended_at) : lastKnown
  const lo = Math.min(start, times.length ? Math.min(...times) : start)
  const hi = Math.max(end, lastKnown)
  return { startMs: lo, endMs: Math.max(hi, lo + 1000) } // never a zero-width axis
}

const clamp01 = (v: number): number => Math.min(1, Math.max(0, v))

export function buildTimeline(report: SafetyReport): TimelineModel {
  const span = reportSpan(report)
  const width = span.endMs - span.startMs
  const byCategory = new Map<string, Lane>()
  let capped = false
  report.timeline.forEach((e, index) => {
    let lane = byCategory.get(e.category)
    if (!lane) {
      lane = { category: e.category, count: 0, marks: [] }
      byCategory.set(e.category, lane)
    }
    lane.count += 1
    if (lane.marks.length < MAX_MARKS_PER_LANE) {
      lane.marks.push({ at: clamp01((Date.parse(e.wall_ts) - span.startMs) / width), confidence: e.confidence, index })
    } else capped = true
  })
  const lanes = [...byCategory.values()].sort((a, b) => b.count - a.count || a.category.localeCompare(b.category))
  const holds = report.holds.map((h) => ({
    from: clamp01((Date.parse(h.start_ts) - span.startMs) / width),
    to: clamp01(((h.end_ts ? Date.parse(h.end_ts) : span.endMs) - span.startMs) / width),
  }))
  return { span, lanes, holds, capped }
}

/** `3725` -> `1 h 2 min 5 s`; `45` -> `45 s`; null -> "-". */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return "-"
  const s = Math.round(seconds)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const r = s % 60
  if (h > 0) return `${h} h ${m} min ${r} s`
  if (m > 0) return `${m} min ${r} s`
  return `${r} s`
}

/** Seconds into the session (`+01:23`) for an event, relative to the span start. */
export function offsetLabel(wallTs: string, span: Span): string {
  const s = Math.max(0, Math.round((Date.parse(wallTs) - span.startMs) / 1000))
  const mm = String(Math.floor(s / 60)).padStart(2, "0")
  return `+${mm}:${String(s % 60).padStart(2, "0")}`
}

/** Seconds a hold lasted (open holds run to the end of the session). Null when unknown. */
export function holdSeconds(start: string, end: string | null, span: Span): number {
  const to = end ? Date.parse(end) : span.endMs
  return Math.max(0, (to - Date.parse(start)) / 1000)
}

export function totalEvents(report: SafetyReport): number {
  return report.timeline.filter((e) => e.category !== "system_status").length // notices are not protections
}

export function totalHoldSeconds(report: SafetyReport, span: Span): number {
  return report.holds.reduce((sum, h) => sum + holdSeconds(h.start_ts, h.end_ts, span), 0)
}

/** Categories by count, biggest first, for the totals grid. */
export function categoryRows(report: SafetyReport): { category: string; count: number; mean: number }[] {
  return Object.entries(report.totals.by_category)
    .map(([category, v]) => ({ category, count: v.count, mean: v.mean_confidence }))
    .sort((a, b) => b.count - a.count || a.category.localeCompare(b.category))
}

/** Highest-confidence events first: "risk peaks". */
export function peakEvents(report: SafetyReport, limit = 5): ReportEvent[] {
  return [...report.timeline].sort((a, b) => b.confidence - a.confidence).slice(0, limit)
}
