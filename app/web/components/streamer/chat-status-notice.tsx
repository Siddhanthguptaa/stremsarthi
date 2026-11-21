"use client"

import { AlertTriangle } from "lucide-react"

import { chatStatusText } from "@/lib/chat-status"
import { useLanguage } from "@/components/shell/language-provider"

/** Plain-language chat shield problems (fixed codes only). Renders nothing when there are none. */
export function ChatStatusNotice({ codes }: { codes: readonly string[] }) {
  const { lang } = useLanguage()
  const lines = codes.map((c) => ({ code: c, text: chatStatusText(c, lang) })).filter((l): l is { code: string; text: string } => l.text !== null)
  if (lines.length === 0) return null
  return (
    <div role="status" data-testid="chat-status" className="space-y-1 rounded-lg border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm">
      {lines.map((l) => (
        <p key={l.code} className="flex items-start gap-2">
          <AlertTriangle aria-hidden className="mt-0.5 size-4 shrink-0" />
          <span>{l.text}</span>
        </p>
      ))}
    </div>
  )
}
