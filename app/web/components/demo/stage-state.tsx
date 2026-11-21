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

interface StageStateChipProps {
  kind: ChipKind
  /** Lanes still catching up while the stream is live (never turns the chip into SLATE). */
  catchingUp?: readonly string[]
  /** A smaller chip for the page header. */
  compact?: boolean
}

/** The LIVE / HOLD / SLATE chip. Text and icon carry the state, colour only backs it up. */
export function StageStateChip({ kind, catchingUp = [], compact = false }: StageStateChipProps) {
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
      hint = catchingUp.length > 0 ? d("state.live.catching").replace("{lanes}", catchingUp.join(", ")) : d(HINT.live)
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
    <div className={cn("space-y-1", compact && "sm:text-right")} data-testid="stage-state" data-state={state === "other" ? kind : state}>
      <p
        role="status"
        aria-label={d("state.label")}
        className={cn(
          "inline-flex items-center gap-2 rounded-full font-extrabold tracking-wide",
          compact ? "min-h-10 px-4 text-xl" : "min-h-14 gap-3 px-6 text-3xl",
          tone,
        )}
      >
        <span className="sr-only">{d("state.label")}: </span>
        <Icon aria-hidden className={cn("shrink-0", compact ? "size-5" : "size-7", Icon === Loader2 && "animate-spin motion-reduce:animate-none")} />
        <span>
          {label}
          {labelAlt ? (
            <span lang={other} className={cn("font-semibold opacity-90", compact ? "ml-2 text-sm" : "ml-3 text-base")}>
              {labelAlt}
            </span>
          ) : null}
        </span>
      </p>
      {hint ? <p className={cn("text-muted-foreground", compact ? "max-w-xs text-xs sm:ml-auto" : "text-sm")}>{hint}</p> : null}
    </div>
  )
}
