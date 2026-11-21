"use client"

import * as React from "react"

import { formatTime } from "@/lib/format"
import { SESSION_ID_RE } from "@/lib/report-api"
import type { SafetyReport } from "@/lib/report-types"
import { formatDuration, reportSpan } from "@/lib/report-view"
import { RequireToken } from "@/components/shell/auth-provider"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { ExportBar } from "@/components/report/export-bar"
import { ChatSection, HoldsSection, LatencySection, NotesSection, RiskSection } from "@/components/report/sections"
import { Timeline } from "@/components/report/timeline"
import { TotalsGrid } from "@/components/report/totals-grid"
import { type ReportState, useReport } from "@/components/report/use-report"
import { useReportText } from "@/components/report/use-report-text"

/** Print layout: the shell's header and skip link are not part of a printout; blocks do not split across pages. */
const PRINT_CSS = `
@media print {
  body > header, body > a, body > div > header, [data-toast-viewport] { display: none !important; }
  main { max-width: none !important; padding: 0 !important; }
  [data-report] section, [data-report] li, [data-report] tr { break-inside: avoid; }
  [data-report] * { box-shadow: none !important; }
}
`

type ProblemCode = Extract<ReportState, { status: "error" }>["code"]

/** The post-stream safety report. Needs a streamer or report token. */
export function ReportPage({ sessionId }: { sessionId: string }) {
  return (
    <RequireToken>
      {SESSION_ID_RE.test(sessionId) ? <Loader sessionId={sessionId} /> : <Problem code="not_found" onRetry={null} />}
    </RequireToken>
  )
}

function Loader({ sessionId }: { sessionId: string }) {
  const { state, reload } = useReport(sessionId)
  const x = useReportText()
  if (state.status === "loading") return <p role="status">{x.tr("report.loading")}</p>
  if (state.status === "error") return <Problem code={state.code} onRetry={state.code === "not_found" ? null : reload} />
  return <ReportView report={state.report} />
}

function Problem({ code, onRetry }: { code: ProblemCode; onRetry: (() => void) | null }) {
  const x = useReportText()
  return (
    <Card>
      <CardContent className="space-y-3 pt-6">
        <h1 className="text-2xl font-bold leading-tight">{x.tr("report.title")}</h1>
        <p role="alert" className="text-sm">
          {x.tr(`report.err.${code}` as const)}
        </p>
        {onRetry ? (
          <Button type="button" className="min-h-11" onClick={onRetry}>
            {x.tr("report.retry")}
          </Button>
        ) : null}
      </CardContent>
    </Card>
  )
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="font-medium tabular-nums">{value}</dd>
    </div>
  )
}

export function ReportView({ report }: { report: SafetyReport }) {
  const x = useReportText()
  const span = React.useMemo(() => reportSpan(report), [report])
  const duration = report.duration_s ?? (span.endMs - span.startMs) / 1000
  const when = (iso: string | null) => (iso ? formatTime(iso, x.lang) : "-")
  const dateLine = report.started_at
    ? new Intl.DateTimeFormat(x.lang === "hi" ? "hi-IN-u-nu-latn" : "en-IN-u-nu-latn", { dateStyle: "medium" }).format(
        new Date(report.started_at),
      )
    : "-"
  return (
    <div className="space-y-5" data-report>
      <style>{PRINT_CSS}</style>
      <header className="space-y-2">
        <h1 className="text-2xl font-bold tracking-tight">{x.tr("report.title")}</h1>
        <p className="text-muted-foreground">{x.tr("report.sub")}</p>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-3 md:grid-cols-6" data-testid="report-meta">
          <Meta label={x.tr("report.session")} value={report.session_id} />
          <Meta label={x.tr("report.when")} value={`${dateLine}, ${when(report.started_at)}`} />
          <Meta label={x.tr("report.ended")} value={when(report.ended_at)} />
          <Meta label={x.tr("report.duration")} value={formatDuration(duration)} />
          <Meta label={x.tr("report.profile")} value={report.profile ?? "-"} />
          <Meta label={x.tr("report.delay")} value={report.delay_seconds === null ? "-" : `${report.delay_seconds} s`} />
        </dl>
      </header>

      {report.ended_at === null ? (
        <p role="status" className="rounded-md border bg-card p-3 text-sm font-medium" data-testid="report-partial">
          {x.tr("report.partial")}
        </p>
      ) : null}

      <ExportBar report={report} />
      <TotalsGrid report={report} span={span} />
      <Timeline report={report} />
      <HoldsSection report={report} span={span} />
      <div className="grid gap-5 md:grid-cols-2">
        <RiskSection report={report} />
        <ChatSection report={report} />
      </div>
      <LatencySection report={report} />
      <NotesSection report={report} />
      <p className="hidden text-xs text-muted-foreground print:block">{x.tr("report.printed")}</p>
    </div>
  )
}
