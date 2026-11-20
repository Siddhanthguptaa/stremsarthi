import type { Metadata } from "next"

import { ModConsole } from "@/components/mod/mod-console"

export const metadata: Metadata = { title: "Moderator" }

export default function Page() {
  return <ModConsole />
}
