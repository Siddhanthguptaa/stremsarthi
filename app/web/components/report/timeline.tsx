"use client"

import * as React from "react"
import { flushSync } from "react-dom"

import { formatPercent, formatTime } from "@/lib/format"
import type { SafetyReport } from "@/lib/report-types"
import { buildTimeline, offsetLabel, type TimelineModel } from "@/lib/report-view"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card"
import { useReportText } from "@/components/report/use-report-text"

const PAGE = 100

function LaneChart({ model, report }: { model: TimelineModel; report: SafetyReport }) {
  const x = useReportText()
  const summary = model.lanes.map((l) => `${x.category(l.category)}: ${l.count}`).join(", ")
  return (
    <div role="img" aria-label={`${x.tr("report.timeline.chart")} ${summary}`} className="space-y-1" data-testid="report-chart">
      {model.lanes.map((lane) => (
        <div key={lane.category} className="grid items-center gap-x-3 gap-y-0.5 sm:grid-cols-[10rem_1fr]">
          <span className="text-sm font-medium">
            {x.category(lane.category)} <span className="text-muted-foreground tabular-nums">({lane.count})</span>
          </span>
          <div className="relative h-6 overflow-hidden rounded border bg-muted/40 [print-color-adjust:exact]">
            {model.holds.map((h, i) => (
              <span
                key={`h${i}`}
                aria-hidden
                className="absolute inset-y-0 bg-destructive/20"
                style={{ left: `${h.from * 100}%`, width: `${Math.max(0.5, (h.to - h.from) * 100)}%` }}
              />
            ))}
            {lane.marks.map((m) => (
              <span
                key={m.index}
                aria-hidden
                data-mark
                className="absolute inset-y-1 w-1.5 -translate-x-1/2 rounded-sm bg-primary [print-color-adjust:exact]"
                style={{ left: `${m.at * 100}%`, opacity: 0.35 + 0.65 * m.confidence }}
              />
            ))}
          </div>
        </div>
      ))}
      <div className="grid gap-x-3 sm:grid-cols-[10rem_1fr]" aria-hidden>
        <span />
        <div className="flex justify-between text-xs text-muted-foreground">
          <span>
            {x.tr("report.timeline.axisStart")} {formatTime(new Date(model.span.startMs).toISOString(), x.lang)}
          </span>
          <span>
            {x.tr("report.timeline.axisEnd")} {formatTime(new Date(model.span.endMs).toISOString(), x.lang)}
          </span>
        </div>
      </div>
      {report.holds.length > 0 ? <p className="sr-only">{x.tr("report.holds")}: {report.holds.length}</p> : null}
    </div>
  )
}

/** Category lanes over time (every mark is an event) and the full event list as a table. Category, time, confidence only. */
export function Timeline({ report }: { report: SafetyReport }) {
  const x = useReportText()
  const model = React.useMemo(() => buildTimeline(report), [report])
  const [shown, setShown] = React.useState(PAGE)
  const events = report.timeline
  const visible = events.slice(0, shown)
  const total = events.length
  // A printout lists every event, not just the first page.
  React.useEffect(() => {
    const open = () => flushSync(() => setShown(Math.max(PAGE, total)))
    const close = () => setShown(PAGE)
    window.addEventListener("beforeprint", open)
    window.addEventListener("afterprint", close)
    return () => {
      window.removeEventListener("beforeprint", open)
      window.removeEventListener("afterprint", close)
    }
  }, [total])
  return (
    <Card>
      <CardHeader>
        <h2 className="text-xl font-semibold leading-none tracking-tight">{x.tr("report.timeline")}</h2>
        <CardDescription>{x.tr("report.events", { n: events.length })}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {events.length === 0 ? (
          <p data-testid="report-empty" className="text-sm text-muted-foreground">
            {x.tr("report.empty")}
          </p>
        ) : (
          <>
            <LaneChart model={model} report={report} />
            {model.capped ? <p className="text-xs text-muted-foreground">{x.tr("report.timeline.capped")}</p> : null}
            <div className="overflow-x-auto rounded-lg border">
              <table className="w-full min-w-[32rem] text-left text-sm" data-testid="report-table">
                <caption className="sr-only">{x.tr("report.timeline.list")}</caption>
                <thead className="bg-muted text-xs uppercase tracking-wide text-muted-foreground">
                  <tr>
                    <th scope="col" className="px-3 py-2 font-medium">{x.tr("report.col.time")}</th>
                    <th scope="col" className="px-3 py-2 font-medium">{x.tr("report.col.category")}</th>
                    <th scope="col" className="px-3 py-2 font-medium">{x.tr("report.col.action")}</th>
                    <th scope="col" className="px-3 py-2 font-medium">{x.tr("report.col.source")}</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">{x.tr("report.col.confidence")}</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {visible.map((e, i) => (
                    <tr key={`${e.event_id}-${i}`}>
                      <td className="whitespace-nowrap px-3 py-2 tabular-nums">
                        <time dateTime={e.wall_ts}>{formatTime(e.wall_ts, x.lang)}</time>
                        <span className="ml-2 text-xs text-muted-foreground">{offsetLabel(e.wall_ts, model.span)}</span>
                      </td>
                      <td className="px-3 py-2 font-medium">{x.category(e.category)}</td>
                      <td className="px-3 py-2">{x.action(e.action)}</td>
                      <td className="px-3 py-2">{x.modality(e.modality)}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{formatPercent(e.confidence)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {shown < events.length ? (
              <Button type="button" variant="outline" className="min-h-11 print:hidden" onClick={() => setShown((n) => n + PAGE)}>
                {x.tr("report.showMore", { n: Math.min(PAGE, events.length - shown) })}
              </Button>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  )
}
