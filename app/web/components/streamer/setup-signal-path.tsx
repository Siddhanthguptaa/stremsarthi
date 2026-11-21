"use client"

import * as React from "react"
import { ArrowRight, Camera, Radio, ShieldCheck } from "lucide-react"

import type { SessionState } from "@/lib/api-streamer"
import { setupText, type SetupTextKey } from "@/lib/i18n-setup"
import { useLanguage } from "@/components/shell/language-provider"

type Tone = "success" | "warning" | "tally" | "muted"

export interface PathInput {
  state: SessionState | null
  /** Destinations are switched on and each has a key. */
  destOk: boolean
  /** At least one destination is switched on. */
  anyDest: boolean
  /** Streaming to a real platform right now. */
  onAir: boolean
}

const TONE: Record<Tone, string> = {
  success: "text-success",
  warning: "text-warning",
  tally: "text-tally",
  muted: "text-muted-foreground",
}

function Node({ icon, name, sub, status, tone }: { icon: React.ReactNode; name: string; sub: string; status: string; tone: Tone }) {
  return (
    <li className="module flex min-w-0 flex-1 items-center gap-3 p-3 md:p-4">
      <span aria-hidden className="grid size-10 shrink-0 place-items-center rounded-md bg-desk-raised">
        {icon}
      </span>
      <div className="min-w-0 flex-1">
        <p className="font-display text-xl font-bold leading-tight">{name}</p>
        <p className="truncate text-xs text-muted-foreground">{sub}</p>
        <p className="mt-1 flex items-center gap-2 text-sm">
          <span aria-hidden className={`led ${TONE[tone]}`} />
          <span>{status}</span>
        </p>
      </div>
    </li>
  )
}

function Joint({ lit }: { lit: boolean }) {
  return (
    <li aria-hidden className={`flex items-center justify-center py-1 md:px-2 ${lit ? "text-success" : "text-muted-foreground"}`}>
      <ArrowRight className="size-5 rotate-90 md:rotate-0" />
    </li>
  )
}

/** Source, Streamify, Destinations: the signal path, each with an LED for readiness. */
export function SignalPath({ state, destOk, anyDest, onAir }: PathInput) {
  const { lang } = useLanguage()
  const s = (key: SetupTextKey) => setupText(lang, key)
  const flowing = state === "live"
  const waiting = state === "starting" || state === "waiting_ingest"
  const failed = state === "failed" || state === "engine_lost"
  const running = flowing || waiting || state === "stopping"

  const source: [SetupTextKey, Tone] = flowing ? ["setup.path.sourceOn", "success"] : waiting ? ["setup.path.sourceWait", "warning"] : ["setup.path.sourceOff", "muted"]
  const engine: [SetupTextKey, Tone] = failed ? ["setup.path.engineBad", "tally"] : running ? ["setup.path.engineOn", "success"] : ["setup.path.engineOff", "muted"]
  const dest: [SetupTextKey, Tone] = onAir
    ? ["setup.path.destLive", "tally"]
    : !anyDest
      ? ["setup.path.destNone", "warning"]
      : destOk
        ? ["setup.path.destReady", "success"]
        : ["setup.path.destMissing", "warning"]

  return (
    <ol aria-label={s("setup.path.label")} className="flex flex-col items-stretch md:flex-row md:items-center">
      <Node icon={<Camera className="size-5 text-video" />} name={s("setup.path.source")} sub={s("setup.path.sourceSub")} status={s(source[0])} tone={source[1]} />
      <Joint lit={flowing} />
      <Node icon={<ShieldCheck className="size-5 text-primary" />} name={s("setup.path.engine")} sub={s("setup.path.engineSub")} status={s(engine[0])} tone={engine[1]} />
      <Joint lit={flowing && destOk} />
      <Node icon={<Radio className="size-5 text-audio" />} name={s("setup.path.dest")} sub="YouTube, Twitch" status={s(dest[0])} tone={dest[1]} />
    </ol>
  )
}
