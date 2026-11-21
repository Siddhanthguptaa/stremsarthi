"use client"

import { CircleCheck, CirclePause, Loader2, ShieldAlert, WifiOff } from "lucide-react"

import type { DemoKey } from "@/lib/demo-i18n"
import { stageState } from "@/lib/demo-stream"
import type { I18nKey } from "@/lib/i18n"
import type { ChipKind } from "@/lib/live-state"
import { cn } from "@/lib/utils"
import { useDemoText } from "@/components/demo/use-demo-text"
import { useLanguage } from "@/components/shell/language-provider"

const OTHER_KEY: Record<Exclude<ChipKind, "live" | "hold" | "protect">, I18nKey> = {
  starting: "chip.starting",
  waiting: "chip.waiting",
  stopping: "chip.stopping",
  error: "chip.error",
  offline: "chip.offline",
  unknown: "chip.unknown",
}

const HINT: Record<"live" | "hold" | "slate", DemoKey> = {
  live: "state.live.hint",
  hold: "state.hold.hint",
  slate: "state.slate.hint",
}

/** The big LIVE / HOLD / SLATE chip. Text and icon carry the state, colour only backs it up. */
export function StageStateChip({ kind }: { kind: ChipKind }) {
  const { t } = useLanguage()
  const { d, alt, other } = useDemoText()
  const state = stageState(kind)

  let tone = "bg-secondary text-secondary-foreground"
  let Icon = Loader2
  let label: string
  let labelAlt: string | null = null
  let hint: string | null = null
  switch (state) {
    case "live":
      tone = "bg-success text-success-foreground"
      Icon = CircleCheck
      label = d("state.live")
      labelAlt = alt("state.live")
      hint = d(HINT.live)
      break
    case "hold":
      tone = "bg-warning text-warning-foreground"
      Icon = CirclePause
      label = d("state.hold")
      labelAlt = alt("state.hold")
      hint = d(HINT.hold)
      break
    case "slate":
      tone = "bg-warning text-warning-foreground"
      Icon = ShieldAlert
      label = d("state.slate")
      labelAlt = alt("state.slate")
      hint = d(HINT.slate)
      break
    default: {
      const key = OTHER_KEY[kind as keyof typeof OTHER_KEY]
      label = t(key)
      if (kind === "offline" || kind === "error") {
        tone = "bg-destructive text-destructive-foreground"
        Icon = WifiOff
      }
    }
  }

  return (
    <div className="space-y-1" data-testid="stage-state" data-state={state === "other" ? kind : state}>
      <p
        role="status"
        aria-label={d("state.label")}
        className={cn("inline-flex min-h-14 items-center gap-3 rounded-full px-6 text-3xl font-extrabold tracking-wide", tone)}
      >
        <span className="sr-only">{d("state.label")}: </span>
        <Icon aria-hidden className={cn("size-7 shrink-0", Icon === Loader2 && "animate-spin motion-reduce:animate-none")} />
        <span>
          {label}
          {labelAlt ? (
            <span lang={other} className="ml-3 text-base font-semibold opacity-90">
              {labelAlt}
            </span>
          ) : null}
        </span>
      </p>
      {hint ? <p className="text-sm text-muted-foreground">{hint}</p> : null}
    </div>
  )
}
