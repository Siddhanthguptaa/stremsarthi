"use client"

import * as React from "react"
import { Gauge } from "lucide-react"

import { categoryKey, formatPercent } from "@/lib/format"
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
 * The strike-guard risk meter as one short card: title, level text and percent, the four-segment bar (still a
 * `role="meter"` with a text value, no animation) and the live warnings as chips (source, category, score).
 */
export function RiskStrip({ view }: { view: RiskView }) {
  const { t } = useLanguage()
  const headingId = React.useId()
  const percent = Math.round(view.score * 100)
  const level = t(RISK_LEVEL_KEY[view.level])
  const reached = RISK_LEVELS.indexOf(view.level) + 1

  return (
    <section aria-labelledby={headingId} data-testid="risk-meter" data-level={view.level} className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border bg-card px-3 py-2">
      <div className="flex items-center gap-3">
        <Gauge aria-hidden className="size-6 shrink-0" />
        <h2 id={headingId} className="text-sm font-semibold leading-tight">
          {t("risk.title")}
        </h2>
        <p className="text-right leading-tight">
          <span className="block text-xl font-bold">{level}</span>
          <span className="text-xs tabular-nums text-muted-foreground">{percent}%</span>
        </p>
      </div>
      <div
        role="meter"
        aria-label={t("risk.title")}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-valuetext={t("risk.valueText").replace("{level}", level).replace("{percent}", `${percent}%`)}
        className="grid w-32 shrink-0 grid-cols-4 gap-1"
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
        <ul aria-label={t("risk.active")} className="flex min-w-0 flex-1 flex-wrap gap-1.5">
          {view.signals.map((signal) => (
            <li key={signal.key} className="inline-flex items-center gap-1.5 rounded-full border bg-background px-3 py-1 text-sm">
              <span className="font-medium">{t(RISK_SOURCE_KEY[signal.source])}</span>
              <span className="text-muted-foreground">{t(categoryKey(signal.category))}</span>
              <span className="font-semibold tabular-nums">
                <span className="sr-only">{t("alerts.confidence")} </span>
                {formatPercent(signal.score)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
