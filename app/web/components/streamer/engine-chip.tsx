"use client"

import { CircleCheck, CirclePause, CircleX, Loader2, ShieldAlert, WifiOff } from "lucide-react"

import { formatSeconds } from "@/lib/format"
import type { I18nKey } from "@/lib/i18n"
import type { ChipKind } from "@/lib/live-state"
import type { EngineStatus } from "@/lib/wire"
import { cn } from "@/lib/utils"
import { useLanguage } from "@/components/shell/language-provider"

const CHIP: Record<ChipKind, { key: I18nKey; tone: string; icon: typeof CircleCheck }> = {
  live: { key: "chip.live", tone: "bg-success text-success-foreground", icon: CircleCheck },
  protect: { key: "chip.protect", tone: "bg-warning text-warning-foreground", icon: ShieldAlert },
  hold: { key: "chip.hold", tone: "bg-warning text-warning-foreground", icon: CirclePause },
  starting: { key: "chip.starting", tone: "bg-secondary text-secondary-foreground", icon: Loader2 },
  waiting: { key: "chip.waiting", tone: "bg-secondary text-secondary-foreground", icon: Loader2 },
  stopping: { key: "chip.stopping", tone: "bg-secondary text-secondary-foreground", icon: Loader2 },
  error: { key: "chip.error", tone: "bg-destructive text-destructive-foreground", icon: CircleX },
  offline: { key: "chip.offline", tone: "bg-destructive text-destructive-foreground", icon: WifiOff },
  unknown: { key: "chip.unknown", tone: "bg-secondary text-secondary-foreground", icon: Loader2 },
}

interface EngineChipProps {
  kind: ChipKind
  /** Present only while the engine is reporting (fresh). */
  engine: EngineStatus | null
}

/** LIVE / PROTECT / HOLD chip plus the engine's health numbers. Text and icon carry the state, not colour alone. */
export function EngineChip({ kind, engine }: EngineChipProps) {
  const { t } = useLanguage()
  const { key, tone, icon: Icon } = CHIP[kind]
  const spinning = kind === "starting" || kind === "waiting" || kind === "stopping" || kind === "unknown"
  const s = t("engine.unit.s")

  return (
    <section aria-label={t("chip.label")} className="space-y-3">
      <p
        role="status"
        data-testid="engine-chip"
        data-kind={kind}
        className={cn("inline-flex min-h-11 items-center gap-2 rounded-full px-5 text-lg font-bold", tone)}
      >
        <Icon aria-hidden className={cn("size-5", spinning && "animate-spin")} />
        <span className="sr-only">{t("chip.label")}: </span>
        {t(key)}
      </p>
      {engine ? (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-4">
          <Metric label={t("engine.delay")} value={`${formatSeconds(engine.delay_s_p50)} ${s}`} />
          <Metric label={t("engine.fps")} value={formatSeconds(engine.fps, 0)} />
          <Metric
            label={t("engine.protect")}
            value={`${formatSeconds(engine.protect_seconds)} ${s}`}
            alert={engine.protect_seconds > 0}
          />
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
    <div className="rounded-md border bg-card px-3 py-2">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={cn("text-base font-semibold tabular-nums", alert && "text-warning")}>{value}</dd>
    </div>
  )
}
