import type { Metadata } from "next"

import { DemoConsole } from "@/components/demo/demo-console"

export const metadata: Metadata = { title: "Demo" }

export default function Page() {
  return <DemoConsole />
}
