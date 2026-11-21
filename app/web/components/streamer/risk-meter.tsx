"use client"

import { Gauge } from "lucide-react"

import { formatPercent, categoryKey } from "@/lib/format"
import { RISK_LEVELS, RISK_LEVEL_KEY, RISK_SOURCE_KEY, type RiskView } from "@/lib/risk"
import type { RiskLevel } from "@/lib/wire"
import { cn } from "@/lib/utils"
import { useLanguage } from "@/components/shell/language-provider"

const TONE: Record<RiskLevel, string> = {
  green: "bg-success",
  yellow: "bg-warning",
  orange: "bg-warning",
  red: "bg-destructive",
}

/**
 * The strike-guard risk meter. `role="meter"` with a text level (never colour alone), a segmented bar that
 * does not animate, and the live warnings as category, kind and score (no labels, no text, no media).
 */
export function RiskMeter({ view }: { view: RiskView }) {
  const { t } = useLanguage()
  const percent = Math.round(view.score * 100)
  const level = t(RISK_LEVEL_KEY[view.level])
  const reached = RISK_LEVELS.indexOf(view.level) + 1

  return (
    <section aria-labelledby="risk-heading" className="space-y-3 rounded-lg border bg-card p-4" data-testid="risk-meter" data-level={view.level}>
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 id="risk-heading" className="flex items-center gap-2 text-lg font-semibold">
            <Gauge aria-hidden className="size-5" />
            {t("risk.title")}
          </h2>
          <p className="text-sm text-muted-foreground">{t("risk.desc")}</p>
        </div>
        <p className="shrink-0 text-right">
          <span className="block text-xl font-bold leading-tight">{level}</span>
          <span className="text-sm tabular-nums text-muted-foreground">{percent}%</span>
        </p>
      </div>

      <div
        role="meter"
        aria-label={t("risk.title")}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-valuetext={t("risk.valueText").replace("{level}", level).replace("{percent}", `${percent}%`)}
        className="grid grid-cols-4 gap-1"
      >
        {RISK_LEVELS.map((name, index) => (
          <div
            key={name}
            aria-hidden
            data-filled={index < reached ? "true" : "false"}
            className={cn("h-3 rounded-sm", index < reached ? TONE[view.level] : "bg-secondary ring-1 ring-input")}
          />
        ))}
      </div>

      {view.signals.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("risk.none")}</p>
      ) : (
        <div className="space-y-1">
          <h3 className="text-sm font-medium">{t("risk.active")}</h3>
          <ul className="space-y-1">
            {view.signals.map((signal) => (
              <li key={signal.key} className="flex items-center justify-between gap-3 rounded-md border bg-background px-3 py-2 text-sm">
                <span className="min-w-0">
                  <span className="block font-medium leading-snug">{t(RISK_SOURCE_KEY[signal.source])}</span>
                  <span className="text-muted-foreground">{t(categoryKey(signal.category))}</span>
                </span>
                <span className="shrink-0 font-semibold tabular-nums">
                  <span className="sr-only">{t("alerts.confidence")} </span>
                  {formatPercent(signal.score)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
