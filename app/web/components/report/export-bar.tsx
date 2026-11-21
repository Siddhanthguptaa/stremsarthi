"use client"

import { Download, Printer } from "lucide-react"

import { downloadReportJson } from "@/lib/report-api"
import type { SafetyReport } from "@/lib/report-types"
import { Button } from "@/components/ui/button"
import { toast } from "@/components/ui/use-toast"
import { useReportText } from "@/components/report/use-report-text"

/** Download the sanitised report as JSON, or print it (the print layout hides the navigation and these buttons). */
export function ExportBar({ report }: { report: SafetyReport }) {
  const x = useReportText()
  return (
    <div className="space-y-2 print:hidden" data-testid="report-export">
      <div className="flex flex-wrap gap-2" role="group" aria-label={x.tr("report.export")}>
        <Button
          type="button"
          className="min-h-11"
          onClick={() => {
            downloadReportJson(report)
            toast({ title: x.tr("report.export.done") })
          }}
        >
          <Download aria-hidden />
          {x.tr("report.export.json")}
        </Button>
        <Button type="button" variant="outline" className="min-h-11" onClick={() => window.print()}>
          <Printer aria-hidden />
          {x.tr("report.export.print")}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">{x.tr("report.export.hint")}</p>
    </div>
  )
}
