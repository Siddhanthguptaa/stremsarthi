"use client"

import type { SessionView } from "@/lib/api-streamer"
import { bannerKind } from "@/lib/golive-text"
import { deskText } from "@/lib/i18n-live-desk"
import type { ChipKind } from "@/lib/live-state"
import type { ConnectionState } from "@/lib/ws"
import type { EngineStatus } from "@/lib/wire"
import { cn } from "@/lib/utils"
import { ConnectionBanner } from "@/components/shell/connection-banner"
import { useLanguage } from "@/components/shell/language-provider"
import { ChatStatusNotice } from "@/components/streamer/chat-status-notice"
import { EngineChip } from "@/components/streamer/engine-chip"
import { LiveBanner } from "@/components/streamer/live-banner"

export type TallyKind = "on-air" | "holding" | "off-air"

export function tallyKind(held: boolean, session: Pick<SessionView, "state" | "output" | "destinations"> | null): TallyKind {
  if (held) return "holding"
  return bannerKind(session) === "live" ? "on-air" : "off-air"
}

/** hh:mm:ss from a start time and now; null when there is no start. */
export function elapsed(startedAt: string | null | undefined, now: number): string | null {
  if (!startedAt) return null
  const t = Date.parse(startedAt)
  if (!Number.isFinite(t)) return null
  const total = Math.max(0, Math.floor((now - t) / 1000))
  const p = (n: number) => String(n).padStart(2, "0")
  return `${p(Math.floor(total / 3600))}:${p(Math.floor((total % 3600) / 60))}:${p(total % 60)}`
}

const TONE: Record<TallyKind, string> = {
  "on-air": "text-tally",
  holding: "text-primary",
  "off-air": "text-muted-foreground",
}

interface Props {
  session: SessionView | null
  held: boolean
  now: number
  chip: ChipKind
  engine: EngineStatus | null
  connection: ConnectionState
  apiDown: boolean
  chatCodes: readonly string[]
}

/** The tally strip: ON AIR / HOLDING / OFF AIR, the session clock, where it goes, and the engine numbers, in one module. */
export function LiveTallyStrip({ session, held, now, chip, engine, connection, apiDown, chatCodes }: Props) {
  const { lang } = useLanguage()
  const kind = tallyKind(held, session)
  const word = deskText(lang, kind === "on-air" ? "tally.onAir" : kind === "holding" ? "tally.holding" : "tally.offAir")
  const clock = kind === "off-air" ? null : elapsed(session?.started_at, now)
  return (
    <section className="module space-y-3 p-4" data-testid="tally-strip" data-tally={kind}>
      <div className="flex flex-col gap-x-8 gap-y-3 md:flex-row md:items-center">
        <div className="flex shrink-0 items-center gap-3">
          <span aria-hidden className={cn("led size-4", TONE[kind])} />
          <p className={cn("font-display text-4xl font-extrabold uppercase leading-none", TONE[kind])}>{word}</p>
          {clock ? <p className="timecode text-3xl font-bold leading-none">{clock}</p> : null}
        </div>
        <LiveBanner session={session} className="md:flex-1" />
        <EngineChip kind={chip} engine={engine} className="md:justify-end" />
      </div>
      <ConnectionBanner state={connection} apiDown={apiDown} />
      <ChatStatusNotice codes={chatCodes} />
    </section>
  )
}
