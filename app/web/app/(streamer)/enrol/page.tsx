import type { Metadata } from "next"

import { EnrolScreen } from "@/components/enrollment/enrol-panel"

export const metadata: Metadata = { title: "Faces" }

export default function Page() {
  return <EnrolScreen />
}
