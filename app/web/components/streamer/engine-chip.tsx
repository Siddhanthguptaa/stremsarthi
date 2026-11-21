"use client"

import { CircleCheck, CirclePause, CircleX, Loader2, ShieldAlert, WifiOff } from "lucide-react"

import { formatSeconds } from "@/lib/format"
import type { I18nKey } from "@/lib/i18n"
import type { ChipKind } from "@/lib/live-state"
import type { EngineStatus } from "@/lib/wire"
import { cn } from "@/lib/utils"
import { useLanguage } from "@/components/shell/language-provider"

const CHIP: Record<ChipKind, { key: I18nKey; tone: string; icon: typeof CircleCheck }> = {
  live: { key: "chip.live", tone: "text-success", icon: CircleCheck },
  protect: { key: "chip.protect", tone: "text-warning", icon: ShieldAlert },
  hold: { key: "chip.hold", tone: "text-primary", icon: CirclePause },
  starting: { key: "chip.starting", tone: "text-muted-foreground", icon: Loader2 },
  waiting: { key: "chip.waiting", tone: "text-muted-foreground", icon: Loader2 },
  stopping: { key: "chip.stopping", tone: "text-muted-foreground", icon: Loader2 },
  error: { key: "chip.error", tone: "text-destructive", icon: CircleX },
  offline: { key: "chip.offline", tone: "text-destructive", icon: WifiOff },
  unknown: { key: "chip.unknown", tone: "text-muted-foreground", icon: Loader2 },
}

interface EngineChipProps {
  kind: ChipKind
  /** Present only while the engine is reporting (fresh). */
  engine: EngineStatus | null
  className?: string
}

/** Engine state plus its health numbers, laid out to sit in the tally strip. Text and icon carry the state, not colour alone. */
export function EngineChip({ kind, engine, className }: EngineChipProps) {
  const { t } = useLanguage()
  const { key, tone, icon: Icon } = CHIP[kind]
  const spinning = kind === "starting" || kind === "waiting" || kind === "stopping" || kind === "unknown"
  const s = t("engine.unit.s")

  return (
    <section aria-label={t("chip.label")} className={cn("flex min-w-0 flex-wrap items-center gap-x-5 gap-y-2", className)}>
      <p role="status" data-testid="engine-chip" data-kind={kind} className={cn("inline-flex min-h-9 items-center gap-2 font-display text-xl font-bold", tone)}>
        <Icon aria-hidden className={cn("size-5", spinning && "animate-spin motion-reduce:animate-none")} />
        <span className="sr-only">{t("chip.label")}: </span>
        {t(key)}
      </p>
      {engine ? (
        <dl className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
          <Metric label={t("engine.delay")} value={`${formatSeconds(engine.delay_s_p50)} ${s}`} />
          <Metric label={t("engine.fps")} value={formatSeconds(engine.fps, 0)} />
          <Metric label={t("engine.protect")} value={`${formatSeconds(engine.protect_seconds)} ${s}`} alert={engine.protect_seconds > 0} />
          <Metric
            label={t("engine.lanes")}
            value={engine.lanes_stale.length > 0 ? engine.lanes_stale.join(", ") : t("engine.none")}
            alert={engine.lanes_stale.length > 0}
          />
        </dl>
      ) : null}
    </section>
  )
}

function Metric({ label, value, alert = false }: { label: string; value: string; alert?: boolean }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={cn("timecode text-lg font-semibold", alert && "text-warning")}>{value}</dd>
    </div>
  )
}
