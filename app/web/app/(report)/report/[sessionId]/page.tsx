import type { Metadata } from "next"

import { ReportPage } from "@/components/report/report-page"

export const metadata: Metadata = { title: "Safety report" }

export default async function Page({ params }: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = await params
  // The id is validated again in the client (letters, digits, - and _ only) before it is used in a request.
  return <ReportPage sessionId={sessionId.slice(0, 64)} />
}
