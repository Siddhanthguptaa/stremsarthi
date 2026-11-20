import type { Metadata } from "next"

import { LiveDashboard } from "@/components/streamer/live-dashboard"

export const metadata: Metadata = { title: "Live" }

export default function Page() {
  return <LiveDashboard />
}
