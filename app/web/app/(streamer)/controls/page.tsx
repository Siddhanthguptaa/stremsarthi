import type { Metadata } from "next"

import { ControlsScreen } from "@/components/streamer/controls-panel"

export const metadata: Metadata = { title: "Controls" }

export default function Page() {
  return <ControlsScreen />
}
