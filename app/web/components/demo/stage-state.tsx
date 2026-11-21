"use client"

import { Loader2, WifiOff } from "lucide-react"

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

/** The LIVE / HOLD / SLATE readout: an LED, the word and a hint. Text carries the state, colour only backs it up. */
export function StageStateChip({ kind, catchingUp = [], compact = false }: StageStateChipProps) {
  const { t } = useLanguage()
  const { d, alt, other } = useDemoText()
  const state = stageState(kind)

  let led = "text-muted-foreground"
  let spinner = true
  let label: string
  let labelAlt: string | null = null
  let hint: string | null = null
  switch (state) {
    case "live":
      led = "text-success"
      spinner = false
      label = d("state.live")
      labelAlt = alt("state.live")
      hint = catchingUp.length > 0 ? d("state.live.catching").replace("{lanes}", catchingUp.join(", ")) : d(HINT.live)
      break
    case "hold":
      led = "text-warning"
      spinner = false
      label = d("state.hold")
      labelAlt = alt("state.hold")
      hint = d(HINT.hold)
      break
    case "slate":
      led = "text-warning"
      spinner = false
      label = d("state.slate")
      labelAlt = alt("state.slate")
      hint = d(HINT.slate)
      break
    default: {
      const key = OTHER_KEY[kind as keyof typeof OTHER_KEY]
      label = t(key)
      if (kind === "offline" || kind === "error") {
        led = "text-destructive"
        spinner = false
      }
    }
  }
  const failed = kind === "offline" || kind === "error"

  return (
    <div className={cn("space-y-1", compact && "sm:text-right")} data-testid="stage-state" data-state={state === "other" ? kind : state}>
      <p
        role="status"
        aria-label={d("state.label")}
        className={cn(
          "module inline-flex items-center gap-3 font-display font-extrabold leading-none",
          compact ? "min-h-11 px-4 text-2xl" : "min-h-14 px-5 text-4xl",
        )}
      >
        <span className="sr-only">{d("state.label")}: </span>
        {spinner ? (
          <Loader2 aria-hidden className="size-5 shrink-0 animate-spin motion-reduce:animate-none" />
        ) : failed ? (
          <WifiOff aria-hidden className={cn("size-5 shrink-0", led)} />
        ) : (
          <span aria-hidden className={cn("led", led)} />
        )}
        <span>
          {label}
          {labelAlt ? (
            <span lang={other} className={cn("ml-3 font-sans font-medium text-muted-foreground", compact ? "text-sm" : "text-base")}>
              {labelAlt}
            </span>
          ) : null}
        </span>
      </p>
      {hint ? <p className={cn("text-muted-foreground", compact ? "max-w-xs text-xs sm:ml-auto" : "text-sm")}>{hint}</p> : null}
    </div>
  )
}
