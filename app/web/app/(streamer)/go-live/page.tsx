import type { Metadata } from "next"

import { ScreenPlaceholder } from "@/components/shell/screen-placeholder"

export const metadata: Metadata = { title: "Go live" }

export default function Page() {
  return <ScreenPlaceholder titleKey="page.goLive.title" descKey="page.goLive.desc" />
}
