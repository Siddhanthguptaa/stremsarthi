"use client"

import * as React from "react"
import { flushSync } from "react-dom"

import { formatPercent, formatTime } from "@/lib/format"
import { deskText } from "@/lib/i18n-demo-desk"
import type { SafetyReport } from "@/lib/report-types"
import { buildTimeline, offsetLabel, type TimelineModel } from "@/lib/report-view"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card"
import { useReportText } from "@/components/report/use-report-text"

const PAGE = 100

const MOSAIC: Record<string, string> = { video: "mosaic-video", audio: "mosaic-audio", chat: "mosaic-chat" }
const LED: Record<string, string> = { video: "text-video", audio: "text-audio", chat: "text-chat" }
const mosaicFor = (modality: string): string => MOSAIC[modality] ?? "mosaic-video"

/** One tick on a strip: a small mosaic block, brighter when the confidence is higher. Position and confidence only. */
function Tick({ at, confidence, modality, tall = false, mark = false }: { at: number; confidence: number; modality: string; tall?: boolean; mark?: boolean }) {
  return (
    <span
      aria-hidden
      {...(mark ? { "data-mark": true } : { "data-tick": true })}
      className={`mosaic ${mosaicFor(modality)} absolute w-1.5 -translate-x-1/2 rounded-[1px] [print-color-adjust:exact] ${tall ? "inset-y-1.5" : "inset-y-1"}`}
      style={{ left: `${at * 100}%`, opacity: 0.4 + 0.6 * confidence }}
    />
  )
}

function HoldBands({ model }: { model: TimelineModel }) {
  return (
    <>
      {model.holds.map((h, i) => (
        <span
          key={`h${i}`}
          aria-hidden
          className="absolute inset-y-0 bg-destructive/25 [print-color-adjust:exact]"
          style={{ left: `${h.from * 100}%`, width: `${Math.max(0.5, (h.to - h.from) * 100)}%` }}
        />
      ))}
    </>
  )
}

/** The whole session as one strip: every event as a mosaic tick, holds as red bands, start and end as timecodes. */
function SessionStrip({ model, report }: { model: TimelineModel; report: SafetyReport }) {
  const x = useReportText()
  const span = model.span
  const width = span.endMs - span.startMs
  const clamp = (v: number) => Math.min(1, Math.max(0, v))
  return (
    <div className="space-y-1.5" aria-hidden data-testid="report-strip">
      <p className="text-sm font-medium">{deskText(x.lang, "report.strip")}</p>
      <div className="relative h-12 overflow-hidden rounded-md bg-background/70 [print-color-adjust:exact]">
        <HoldBands model={model} />
        {report.timeline.slice(0, 600).map((e, i) => (
          <Tick key={i} at={clamp(width > 0 ? (Date.parse(e.wall_ts) - span.startMs) / width : 0)} confidence={e.confidence} modality={e.modality} tall />
        ))}
      </div>
      <div className="timecode flex justify-between text-base text-muted-foreground">
        <span>{formatTime(new Date(span.startMs).toISOString(), x.lang)}</span>
        <span>{formatTime(new Date(span.endMs).toISOString(), x.lang)}</span>
      </div>
      <p className="text-xs text-muted-foreground">{deskText(x.lang, "report.stripHint")}</p>
    </div>
  )
}

function LaneChart({ model, report }: { model: TimelineModel; report: SafetyReport }) {
  const x = useReportText()
  const summary = model.lanes.map((l) => `${x.category(l.category)}: ${l.count}`).join(", ")
  return (
    <div role="img" aria-label={`${x.tr("report.timeline.chart")} ${summary}`} className="space-y-1.5" data-testid="report-chart">
      {model.lanes.map((lane) => {
        const modality = report.timeline[lane.marks[0]?.index ?? 0]?.modality ?? "video"
        return (
          <div key={lane.category} className="grid items-center gap-x-3 gap-y-0.5 sm:grid-cols-[14rem_1fr]">
            <span className="flex items-center gap-2 text-sm font-medium">
              <span aria-hidden className={`led ${LED[modality] ?? "text-video"}`} />
              {x.category(lane.category)} <span className="timecode whitespace-nowrap text-base text-muted-foreground">({lane.count})</span>
            </span>
            <div className="relative h-7 overflow-hidden rounded-sm bg-background/70 [print-color-adjust:exact]">
              <HoldBands model={model} />
              {lane.marks.map((m) => (
                <Tick key={m.index} mark at={m.at} confidence={m.confidence} modality={report.timeline[m.index]?.modality ?? modality} />
              ))}
            </div>
          </div>
        )
      })}
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
        <h2 className="text-xl font-bold leading-none">{x.tr("report.timeline")}</h2>
        <CardDescription>{x.tr("report.events", { n: events.length })}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {events.length === 0 ? (
          <p data-testid="report-empty" className="text-sm text-muted-foreground">
            {x.tr("report.empty")}
          </p>
        ) : (
          <>
            <SessionStrip model={model} report={report} />
            <LaneChart model={model} report={report} />
            {model.capped ? <p className="text-xs text-muted-foreground">{x.tr("report.timeline.capped")}</p> : null}
            <div className="module-row overflow-x-auto">
              <table className="w-full min-w-[32rem] text-left text-sm" data-testid="report-table">
                <caption className="sr-only">{x.tr("report.timeline.list")}</caption>
                <thead className="border-b text-sm text-muted-foreground">
                  <tr>
                    <th scope="col" className="px-3 py-2 font-medium">{x.tr("report.col.time")}</th>
                    <th scope="col" className="px-3 py-2 font-medium">{x.tr("report.col.category")}</th>
                    <th scope="col" className="px-3 py-2 font-medium">{x.tr("report.col.action")}</th>
                    <th scope="col" className="px-3 py-2 font-medium">{x.tr("report.col.source")}</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">{x.tr("report.col.confidence")}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/60">
                  {visible.map((e, i) => (
                    <tr key={`${e.event_id}-${i}`}>
                      <td className="whitespace-nowrap px-3 py-2">
                        <time dateTime={e.wall_ts} className="timecode text-base">{formatTime(e.wall_ts, x.lang)}</time>
                        <span className="ml-2 text-xs text-muted-foreground">{offsetLabel(e.wall_ts, model.span)}</span>
                      </td>
                      <td className="px-3 py-2 font-medium">{x.category(e.category)}</td>
                      <td className="px-3 py-2">{x.action(e.action)}</td>
                      <td className="px-3 py-2">
                        <span className="inline-flex items-center gap-2">
                          <span aria-hidden className={`led ${LED[e.modality] ?? "text-muted-foreground"}`} />
                          {x.modality(e.modality)}
                        </span>
                      </td>
                      <td className="timecode px-3 py-2 text-right text-lg font-bold">{formatPercent(e.confidence)}</td>
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
