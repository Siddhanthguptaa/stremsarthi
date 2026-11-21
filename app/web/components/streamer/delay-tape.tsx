"use client"

import type { Alert } from "@/lib/live-state"
import { deskText } from "@/lib/i18n-live-desk"
import { useLanguage } from "@/components/shell/language-provider"

export const TAPE_WINDOW_S = 10
export const DEFAULT_DELAY_S = 4
const MAX_BLOCKS = 40

export interface TapeEvent {
  id: string
  modality: "video" | "audio" | "chat"
  /** 0..100, the distance from the "detected" edge. */
  left: number
  /** Width in percent of the track. */
  width: number
  ageS: number
}

/** The tape length in seconds: 10 s, longer when the delay is. */
export function tapeWindow(delayS: number): number {
  return Math.max(TAPE_WINDOW_S, Math.ceil(delayS + 3))
}

/** The delay to draw: the engine's, when it is a sane number. */
export function tapeDelay(delayS: number | null | undefined): number {
  return typeof delayS === "number" && Number.isFinite(delayS) && delayS > 0 && delayS < 30 ? delayS : DEFAULT_DELAY_S
}

/** Position in percent for an age in seconds (clamped to the track). */
export function tapePosition(ageS: number, windowS: number): number {
  return Math.min(100, Math.max(0, (ageS / windowS) * 100))
}

/** The masks that are still on the tape, oldest edge first dropped. Only video, audio and chat events. */
export function tapeEvents(alerts: readonly Alert[], now: number, windowS: number): TapeEvent[] {
  const out: TapeEvent[] = []
  for (const a of alerts) {
    if (a.modality !== "video" && a.modality !== "audio" && a.modality !== "chat") continue
    const t = Date.parse(a.at)
    if (!Number.isFinite(t)) continue
    const ageS = (now - t) / 1000
    if (ageS > windowS) continue
    const clamped = Math.max(0, ageS)
    const dur = a.durationMs === null ? 0 : a.durationMs / 1000
    const width = Math.min(100 - tapePosition(clamped, windowS), Math.max(2, (dur / windowS) * 100))
    out.push({ id: a.id, modality: a.modality, left: tapePosition(clamped, windowS), width, ageS: clamped })
    if (out.length >= MAX_BLOCKS) break
  }
  return out
}

export function countByModality(events: readonly TapeEvent[]): Record<"video" | "audio" | "chat", number> {
  const c = { video: 0, audio: 0, chat: 0 }
  for (const e of events) c[e.modality] += 1
  return c
}

/** The signature element: masks travelling from "detected" toward "on air" along the delay window. */
export function DelayTape({ alerts, now, delayS }: { alerts: readonly Alert[]; now: number; delayS: number | null | undefined }) {
  const { lang } = useLanguage()
  const T = (k: Parameters<typeof deskText>[1], v?: Record<string, string | number>) => deskText(lang, k, v)
  const delay = tapeDelay(delayS)
  const windowS = tapeWindow(delay)
  const events = tapeEvents(alerts, now, windowS)
  const counts = countByModality(events)
  const onAirLeft = tapePosition(delay, windowS)
  const ticks = Array.from({ length: windowS + 1 }, (_, i) => i)
  const summary = T("tape.summary", { window: windowS, total: events.length, video: counts.video, audio: counts.audio, chat: counts.chat, delay: delay.toFixed(1) })

  return (
    <section aria-labelledby="tape-heading" className="module space-y-3 p-4" data-testid="delay-tape">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4">
        <h2 id="tape-heading" className="text-xl font-bold">
          {T("tape.title")}
        </h2>
        <p className="text-sm text-muted-foreground">{T("tape.desc")}</p>
      </div>

      <div role="img" aria-label={summary} className="module-row relative h-28 px-0">
        {/* tick ruler */}
        <div aria-hidden className="absolute bottom-0 left-4 right-4 h-6 border-t">
          {ticks.map((s) => (
            <span key={s} className="absolute top-0 -translate-x-1/2" style={{ left: `${tapePosition(s, windowS)}%` }}>
              <span className="mx-auto block h-1.5 w-px bg-border" />
              {s % 2 === 0 ? <span className="timecode whitespace-nowrap text-xs text-muted-foreground">{s}</span> : null}
            </span>
          ))}
        </div>
        {/* masks */}
        <div aria-hidden className="absolute bottom-6 left-4 right-4 top-0">
          {events.map((e) => (
            <span
              key={e.id}
              data-testid="tape-mask"
              data-modality={e.modality}
              className={`mosaic mosaic-${e.modality} absolute top-4 h-12 min-w-3 rounded-sm`}
              style={{ left: `${e.left}%`, width: `${e.width}%`, transition: "left 1s linear" }}
            />
          ))}
          {/* on-air line */}
          <span className="absolute inset-y-0 w-0.5 bg-tally" style={{ left: `${onAirLeft}%` }} />
          <span className="absolute top-0 -translate-x-full pr-1.5 text-xs font-semibold text-tally" style={{ left: `${onAirLeft}%` }}>
            {T("tape.onAir")}
          </span>
          <span className="absolute left-0 top-0 text-xs font-semibold text-muted-foreground">{T("tape.detected")}</span>
        </div>
      </div>
      {events.length === 0 ? <p className="text-sm text-muted-foreground">{T("tape.empty")}</p> : null}
    </section>
  )
}
