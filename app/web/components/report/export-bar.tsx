"use client"

import * as React from "react"
import { Download, FileText, Printer } from "lucide-react"

import { createReportClient, downloadReportJson, type ExportFormat, reportFileName, saveBlob } from "@/lib/report-api"
import type { SafetyReport } from "@/lib/report-types"
import { useAuth } from "@/components/shell/auth-provider"
import { Button } from "@/components/ui/button"
import { toast } from "@/components/ui/use-toast"
import { useReportText } from "@/components/report/use-report-text"

/**
 * Download the report from the server (`/export?format=json|html`, fetched with the token and saved as a file), or
 * print it (the print layout hides the navigation and these buttons). If the server cannot be reached the JSON is
 * saved from the cleaned report already on the page.
 */
export function ExportBar({ report }: { report: SafetyReport }) {
  const x = useReportText()
  const { getToken, signOut } = useAuth()
  const client = React.useMemo(() => createReportClient({ getToken, onUnauthorized: signOut }), [getToken, signOut])
  const [busy, setBusy] = React.useState<ExportFormat | null>(null)

  const download = async (format: ExportFormat) => {
    setBusy(format)
    try {
      saveBlob(await client.exportReport(report.session_id, format), reportFileName(report.session_id, format))
      toast({ title: x.tr("report.export.done") })
    } catch (err) {
      const unreachable = err instanceof Error && "status" in err && (err as { status: number }).status === 0
      if (format === "json" && unreachable) {
        downloadReportJson(report)
        toast({ title: x.tr("report.export.done"), description: x.tr("report.export.local") })
      } else {
        toast({ variant: "destructive", title: x.tr("report.export.failed") })
      }
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-2 print:hidden" data-testid="report-export">
      <div className="flex flex-wrap gap-2" role="group" aria-label={x.tr("report.export")}>
        <Button type="button" className="min-h-11" disabled={busy !== null} onClick={() => void download("json")}>
          <Download aria-hidden />
          {x.tr("report.export.json")}
        </Button>
        <Button type="button" variant="outline" className="min-h-11" disabled={busy !== null} onClick={() => void download("html")}>
          <FileText aria-hidden />
          {x.tr("report.export.html")}
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
