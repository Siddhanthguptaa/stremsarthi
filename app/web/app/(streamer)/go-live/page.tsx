import type { Metadata } from "next"

import { GoLiveScreen } from "@/components/streamer/go-live-panel"

export const metadata: Metadata = { title: "Go live" }

export default function Page() {
  return <GoLiveScreen />
}
