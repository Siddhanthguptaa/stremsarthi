import type { Metadata } from "next"

import { ScreenPlaceholder } from "@/components/shell/screen-placeholder"

export const metadata: Metadata = { title: "Moderator" }

export default function Page() {
  return <ScreenPlaceholder titleKey="page.mod.title" descKey="page.mod.desc" />
}
