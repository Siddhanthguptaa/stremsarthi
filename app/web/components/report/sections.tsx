"use client"

import { formatPercent, formatSeconds, formatTime } from "@/lib/format"
import type { SafetyReport } from "@/lib/report-types"
import { formatDuration, holdSeconds, peakEvents, type Span } from "@/lib/report-view"
import { Card, CardContent, CardHeader } from "@/components/ui/card"
import { useReportText } from "@/components/report/use-report-text"

function H2({ children }: { children: React.ReactNode }) {
  return <h2 className="text-xl font-bold leading-none">{children}</h2>
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="module-row p-3">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="timecode mt-1 text-3xl font-bold leading-none">{value}</dd>
    </div>
  )
}

/** Holds (when, how long, who set them) and the fail-safe seconds. */
export function HoldsSection({ report, span }: { report: SafetyReport; span: Span }) {
  const x = useReportText()
  const { protect_seconds: protect, audio_failsafe_seconds: audio } = report.failsafe
  return (
    <Card>
      <CardHeader>
        <H2>{x.tr("report.holds")}</H2>
      </CardHeader>
      <CardContent className="space-y-3">
        {report.holds.length === 0 ? (
          <p className="text-sm text-muted-foreground">{x.tr("report.holds.none")}</p>
        ) : (
          <ul className="space-y-1.5" data-testid="report-holds">
            {report.holds.map((h, i) => (
              <li key={i} className="module-row px-3 py-2 text-sm">
                <span className="font-medium">
                  {x.tr("report.holds.item", {
                    start: formatTime(h.start_ts, x.lang),
                    length: h.end_ts ? formatDuration(holdSeconds(h.start_ts, h.end_ts, span)) : x.tr("report.holds.open"),
                  })}
                </span>
                {h.sources.length > 0 ? (
                  <span className="ml-2 text-muted-foreground">
                    {x.tr("report.holds.by")}: {h.sources.join(", ")}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        <dl className="grid gap-3 sm:grid-cols-2">
          <Stat label={x.tr("report.failsafe.protect")} value={formatDuration(protect)} />
          <Stat label={x.tr("report.failsafe.audio")} value={formatDuration(audio)} />
        </dl>
      </CardContent>
    </Card>
  )
}

export function LatencySection({ report }: { report: SafetyReport }) {
  const x = useReportText()
  const { delay_p50_s: p50, delay_p95_s: p95, fps_mean: fps } = report.latency
  return (
    <Card>
      <CardHeader>
        <H2>{x.tr("report.latency")}</H2>
      </CardHeader>
      <CardContent>
        <dl className="grid gap-3 sm:grid-cols-3">
          <Stat label={x.tr("report.latency.p50")} value={p50 === null ? "-" : `${formatSeconds(p50)} s`} />
          <Stat label={x.tr("report.latency.p95")} value={p95 === null ? "-" : `${formatSeconds(p95)} s`} />
          <Stat label={x.tr("report.latency.fps")} value={formatSeconds(fps)} />
        </dl>
      </CardContent>
    </Card>
  )
}

/** Highest risk level, strike warnings and the most confident events. */
export function RiskSection({ report }: { report: SafetyReport }) {
  const x = useReportText()
  const { max_level: level, warnings } = report.risk
  const peaks = peakEvents(report, 5)
  return (
    <Card>
      <CardHeader>
        <H2>{x.tr("report.risk")}</H2>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm">
          {x.tr("report.risk.max")}: <strong>{level ? x.level(level) : "-"}</strong>
        </p>
        {warnings.length === 0 ? (
          <p className="text-sm text-muted-foreground">{x.tr("report.risk.none")}</p>
        ) : (
          <ul className="space-y-1.5" data-testid="report-warnings">
            {warnings.map((w, i) => (
              <li key={i} className="module-row flex flex-wrap gap-x-3 px-3 py-2 text-sm">
                <time dateTime={w.ts} className="timecode text-base">
                  {formatTime(w.ts, x.lang)}
                </time>
                <span className="font-medium">{w.source || "-"}</span>
                <span className="text-muted-foreground">{w.label}</span>
                <span className="ml-auto tabular-nums">{formatPercent(w.score)}</span>
              </li>
            ))}
          </ul>
        )}
        {peaks.length > 0 ? (
          <div className="space-y-1.5">
            <h3 className="text-sm font-semibold">{x.tr("report.risk.peaks")}</h3>
            <ol className="space-y-1 text-sm">
              {peaks.map((e, i) => (
                <li key={`${e.event_id}-${i}`} className="flex gap-x-3">
                  <span className="font-medium">{x.category(e.category)}</span>
                  <time dateTime={e.wall_ts} className="text-muted-foreground tabular-nums">
                    {formatTime(e.wall_ts, x.lang)}
                  </time>
                  <span className="ml-auto tabular-nums">{formatPercent(e.confidence)}</span>
                </li>
              ))}
            </ol>
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}

/** Chat shield counts: by action, by category and what the platforms accepted. Counts only, never message text. */
export function ChatSection({ report }: { report: SafetyReport }) {
  const x = useReportText()
  const { by_action: actions, by_category: cats, platform_actions: pa } = report.chat
  const any = Object.keys(actions).length + Object.keys(cats).length + pa.done + pa.failed + pa.unsupported > 0
  return (
    <Card>
      <CardHeader>
        <H2>{x.tr("report.chat")}</H2>
      </CardHeader>
      <CardContent className="space-y-3">
        {!any ? (
          <p className="text-sm text-muted-foreground">{x.tr("report.chat.none")}</p>
        ) : (
          <>
            <ul className="flex flex-wrap gap-2" data-testid="report-chat">
              {Object.entries(actions).map(([k, n]) => (
                <li key={`a-${k}`} className="module-row px-3 py-1 text-sm">
                  {k} <span className="font-semibold tabular-nums">{n}</span>
                </li>
              ))}
              {Object.entries(cats).map(([k, n]) => (
                <li key={`c-${k}`} className="module-row px-3 py-1 text-sm">
                  {x.category(k)} <span className="font-semibold tabular-nums">{n}</span>
                </li>
              ))}
            </ul>
            <p className="text-sm">
              {x.tr("report.chat.platform")}: {x.tr("report.chat.done")} <strong className="tabular-nums">{pa.done}</strong>,{" "}
              {x.tr("report.chat.failed")} <strong className="tabular-nums">{pa.failed}</strong>, {x.tr("report.chat.unsupported")}{" "}
              <strong className="tabular-nums">{pa.unsupported}</strong>
            </p>
          </>
        )}
      </CardContent>
    </Card>
  )
}

export function NotesSection({ report }: { report: SafetyReport }) {
  const x = useReportText()
  if (report.notes.length === 0) return null
  return (
    <Card>
      <CardHeader>
        <H2>{x.tr("report.notes")}</H2>
      </CardHeader>
      <CardContent>
        <ul className="list-disc space-y-1 pl-5 text-sm" data-testid="report-notes">
          {report.notes.map((n, i) => (
            <li key={i}>{x.note(n)}</li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}
