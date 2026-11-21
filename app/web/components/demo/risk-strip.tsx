"use client"

import * as React from "react"

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
 * The strike-guard risk meter as one desk module: the level in big type, a four-segment bar (still a
 * `role="meter"` with a text value, no animation) and the live warnings as chips (source, category, score).
 */
export function RiskStrip({ view }: { view: RiskView }) {
  const { t } = useLanguage()
  const headingId = React.useId()
  const percent = Math.round(view.score * 100)
  const level = t(RISK_LEVEL_KEY[view.level])
  const reached = RISK_LEVELS.indexOf(view.level) + 1

  return (
    <section aria-labelledby={headingId} data-testid="risk-meter" data-level={view.level} className="module space-y-3 p-4">
      <div className="flex items-end justify-between gap-3">
        <h2 id={headingId} className="text-xl font-bold leading-tight">
          {t("risk.title")}
        </h2>
        <p className="flex items-baseline gap-2 leading-none">
          <span className="font-display text-3xl font-extrabold">{level}</span>
          <span className="timecode text-lg text-muted-foreground">{percent}%</span>
        </p>
      </div>
      <div
        role="meter"
        aria-label={t("risk.title")}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-valuetext={t("risk.valueText").replace("{level}", level).replace("{percent}", `${percent}%`)}
        className="grid w-full grid-cols-4 gap-1"
      >
        {RISK_LEVELS.map((name, index) => (
          <div
            key={name}
            aria-hidden
            data-filled={index < reached ? "true" : "false"}
            className={cn("h-4 rounded-sm", index < reached ? TONE[view.level] : "bg-background/70")}
          />
        ))}
      </div>
      {view.signals.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("risk.none")}</p>
      ) : (
        <ul aria-label={t("risk.active")} className="flex min-w-0 flex-wrap gap-1.5">
          {view.signals.map((signal) => (
            <li key={signal.key} className="module-row inline-flex items-center gap-2 px-3 py-1 text-sm">
              <span className="font-medium">{t(RISK_SOURCE_KEY[signal.source])}</span>
              <span className="text-muted-foreground">{t(categoryKey(signal.category))}</span>
              <span className="timecode text-base font-semibold">
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
