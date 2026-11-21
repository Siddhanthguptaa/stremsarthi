"use client"

import { formatPercent, categoryKey } from "@/lib/format"
import { RISK_LEVELS, RISK_LEVEL_KEY, RISK_SOURCE_KEY, type RiskView } from "@/lib/risk"
import type { RiskLevel } from "@/lib/wire"
import { cn } from "@/lib/utils"
import { useLanguage } from "@/components/shell/language-provider"

const TONE: Record<RiskLevel, string> = {
  green: "bg-success",
  yellow: "bg-warning",
  orange: "bg-warning",
  red: "bg-tally",
}
const TEXT: Record<RiskLevel, string> = {
  green: "text-success",
  yellow: "text-warning",
  orange: "text-warning",
  red: "text-tally",
}

/**
 * The strike-guard risk meter. `role="meter"` with a text level (never colour alone), four tall segments that
 * do not animate, and the live warnings as category, kind and score (no labels, no text, no media).
 */
export function RiskMeter({ view }: { view: RiskView }) {
  const { t } = useLanguage()
  const percent = Math.round(view.score * 100)
  const level = t(RISK_LEVEL_KEY[view.level])
  const reached = RISK_LEVELS.indexOf(view.level) + 1

  return (
    <section aria-labelledby="risk-heading" className="module space-y-3 p-4" data-testid="risk-meter" data-level={view.level}>
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="risk-heading" className="text-xl font-bold">
          {t("risk.title")}
        </h2>
        <p className="timecode text-lg text-muted-foreground">{percent}%</p>
      </div>
      <p className={cn("font-display text-5xl font-extrabold leading-none", TEXT[view.level])}>{level}</p>

      <div
        role="meter"
        aria-label={t("risk.title")}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-valuetext={t("risk.valueText").replace("{level}", level).replace("{percent}", `${percent}%`)}
        className="grid grid-cols-4 gap-1.5"
      >
        {RISK_LEVELS.map((name, index) => (
          <div
            key={name}
            aria-hidden
            data-filled={index < reached ? "true" : "false"}
            className={cn("h-12 rounded-sm", index < reached ? TONE[view.level] : "bg-background/60 ring-1 ring-border")}
          />
        ))}
      </div>
      <p className="text-sm text-muted-foreground">{t("risk.desc")}</p>

      {view.signals.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("risk.none")}</p>
      ) : (
        <div className="space-y-1.5">
          <h3 className="text-base font-bold">{t("risk.active")}</h3>
          <ul className="space-y-1.5">
            {view.signals.map((signal) => (
              <li key={signal.key} className="module-row flex items-center justify-between gap-3 px-3 py-2 text-sm">
                <span className="min-w-0">
                  <span className="block font-medium leading-snug">{t(RISK_SOURCE_KEY[signal.source])}</span>
                  <span className="text-muted-foreground">{t(categoryKey(signal.category))}</span>
                </span>
                <span className="timecode shrink-0 text-lg font-semibold">
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
