import type { Metadata } from "next"

import { ScreenPlaceholder } from "@/components/shell/screen-placeholder"

export const metadata: Metadata = { title: "Safety report" }

export default async function ReportPage({ params }: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = await params
  // The id is only ever rendered as text in a <code> element, never interpolated into HTML or URLs.
  return <ScreenPlaceholder titleKey="page.report.title" descKey="page.report.desc" detail={sessionId.slice(0, 64)} />
}
