"use client"

import { formatDuration, totalHoldSeconds, type Span, categoryRows } from "@/lib/report-view"
import { formatPercent } from "@/lib/format"
import { deskText } from "@/lib/i18n-demo-desk"
import type { SafetyReport } from "@/lib/report-types"
import { cn } from "@/lib/utils"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { useReportText } from "@/components/report/use-report-text"

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="module-row p-3">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="timecode mt-1 text-4xl font-bold leading-none">{value}</dd>
    </div>
  )
}

const SOURCES = [
  { modality: "video", led: "text-video", label: "proof.video" },
  { modality: "audio", led: "text-audio", label: "proof.audio" },
  { modality: "chat", led: "text-chat", label: "proof.chat" },
] as const

function CountList({ title, rows }: { title: string; rows: { key: string; label: string; count: number }[] }) {
  if (rows.length === 0) return null
  return (
    <section aria-label={title} className="space-y-1.5">
      <h3 className="text-base font-bold">{title}</h3>
      <ul className="flex flex-wrap gap-2">
        {rows.map((r) => (
          <li key={r.key} className="module-row px-3 py-1 text-sm">
            {r.label} <span className="timecode text-base font-bold">{r.count}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

/** Headline numbers (one big number per source, then the totals), per-category counts with average confidence, and counts by action and by source. */
export function TotalsGrid({ report, span }: { report: SafetyReport; span: Span }) {
  const x = useReportText()
  const rows = categoryRows(report)
  const events = report.totals.events
  const failsafe = (report.failsafe.protect_seconds ?? 0) + (report.failsafe.audio_failsafe_seconds ?? 0)
  return (
    <Card>
      <CardHeader>
        <h2 className="text-xl font-bold leading-none">{x.tr("report.totals")}</h2>
      </CardHeader>
      <CardContent className="space-y-5">
        <dl className="grid grid-cols-3 gap-3" data-testid="report-sources" aria-label={deskText(x.lang, "report.totalsBySource")}>
          {SOURCES.map(({ modality, led, label }) => (
            <div key={modality} className="module-row p-3" data-modality={modality}>
              <dt className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
                <span aria-hidden className={cn("led", led)} />
                {deskText(x.lang, label)}
              </dt>
              <dd className="timecode mt-1 text-5xl font-extrabold leading-none md:text-6xl">{report.totals.by_modality[modality] ?? 0}</dd>
            </div>
          ))}
        </dl>

        <dl className="grid grid-cols-2 gap-3 md:grid-cols-4" data-testid="report-tiles">
          <Tile label={x.tr("report.totals.events")} value={String(events)} />
          <Tile label={x.tr("report.totals.holds")} value={String(report.holds.length)} />
          <Tile label={x.tr("report.totals.holdTime")} value={formatDuration(totalHoldSeconds(report, span))} />
          <Tile label={x.tr("report.totals.failsafe")} value={formatDuration(failsafe)} />
        </dl>

        {rows.length > 0 ? (
          <section aria-label={x.tr("report.byCategory")} className="space-y-1.5">
            <h3 className="text-base font-bold">{x.tr("report.byCategory")}</h3>
            <ul className="space-y-1.5" data-testid="report-categories">
              {rows.map((r) => (
                <li key={r.category} className="module-row flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 px-3 py-2">
                  <span className="font-medium">{x.category(r.category)}</span>
                  <span className="text-sm">
                    <span className="timecode text-xl font-bold">{r.count}</span>
                    <span className="ml-3 text-muted-foreground">
                      {formatPercent(r.mean)} {x.tr("report.meanConfidence")}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        <div className="grid gap-4 md:grid-cols-2">
          <CountList
            title={x.tr("report.byAction")}
            rows={Object.entries(report.totals.by_action).map(([k, count]) => ({ key: k, label: x.action(k), count }))}
          />
          <CountList
            title={x.tr("report.byModality")}
            rows={Object.entries(report.totals.by_modality).map(([k, count]) => ({ key: k, label: x.modality(k), count }))}
          />
        </div>
      </CardContent>
    </Card>
  )
}
