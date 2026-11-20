"use client"

import { WifiOff } from "lucide-react"

import type { ConnectionState } from "@/lib/ws"
import { useLanguage } from "@/components/shell/language-provider"

/** Offline / connecting / reconnecting / expired notice shared by /live and /mod. Nothing when all is well. */
export function ConnectionBanner({ state, apiDown }: { state: ConnectionState; apiDown: boolean }) {
  const { t } = useLanguage()
  if (apiDown) {
    return (
      <p role="alert" className="flex items-start gap-2 rounded-md border border-destructive bg-card p-3 text-sm font-medium">
        <WifiOff aria-hidden className="mt-0.5 size-5 shrink-0 text-destructive" />
        {t("conn.offline")}
      </p>
    )
  }
  if (state === "open" || state === "idle") return null
  const key = state === "connecting" ? "conn.connecting" : state === "unauthorized" ? "conn.expired" : "conn.reconnecting"
  return (
    <p role="status" className="rounded-md border bg-card p-3 text-sm font-medium">
      {t(key)}
    </p>
  )
}
