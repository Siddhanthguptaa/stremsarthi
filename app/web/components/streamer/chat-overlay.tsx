"use client"

import * as React from "react"
import { EyeOff } from "lucide-react"

import { clipText, type ChatOverlayState, OVERLAY_MAX_MESSAGES, type OverlayMessage } from "@/lib/chat-state"
import { formatTime } from "@/lib/format"
import type { Lang } from "@/lib/i18n"
import { useLanguage } from "@/components/shell/language-provider"

interface ChatOverlayProps {
  state: ChatOverlayState
}

/**
 * The streamer's chat: messages the shield let through, and "N hidden". Hidden messages have no place here
 * (the API never sends their text to the streamer), only the count. Newest first, capped, text only.
 */
export function ChatOverlay({ state }: ChatOverlayProps) {
  const { t, lang } = useLanguage()
  return (
    <section aria-labelledby="chat-heading" className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="chat-heading" className="text-lg font-semibold">
          {t("chat.title")}
        </h2>
        <p
          data-testid="hidden-count"
          className="inline-flex min-h-8 items-center gap-1.5 rounded-full border bg-card px-3 text-sm font-medium"
        >
          <EyeOff aria-hidden className="size-4" />
          <span className="sr-only">{t("chat.hidden")}: </span>
          <span aria-hidden>{t("chat.hiddenCount").replace("{n}", String(state.hiddenCount))}</span>
          <span className="sr-only">{state.hiddenCount}</span>
        </p>
      </div>
      <div
        role="log"
        aria-live="polite"
        aria-relevant="additions"
        aria-label={t("chat.region")}
        tabIndex={0}
        className="max-h-[40vh] min-h-24 overflow-y-auto rounded-lg border bg-card p-2"
        data-testid="chat-overlay"
      >
        {state.messages.length === 0 ? (
          <p className="px-2 py-4 text-center text-sm text-muted-foreground">{t("chat.empty")}</p>
        ) : (
          <ol className="space-y-1.5">
            {state.messages.map((m) => (
              <ChatRow key={m.key} message={m} lang={lang} />
            ))}
          </ol>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        {t("chat.hiddenNote")}
        {state.total > OVERLAY_MAX_MESSAGES ? ` ${t("chat.capped")}` : ""}
      </p>
    </section>
  )
}

const ChatRow = React.memo(function ChatRow({ message, lang }: { message: OverlayMessage; lang: Lang }) {
  const { t } = useLanguage()
  const iso = new Date(message.receivedAt * 1000).toISOString()
  return (
    <li className="rounded-md border bg-background px-3 py-2" data-platform={message.platform}>
      <p className="flex flex-wrap items-baseline gap-x-2 text-sm">
        <span className="font-semibold">{message.author}</span>
        {message.broadcaster ? (
          <span className="rounded border px-1.5 text-xs font-medium">{t("chat.role.broadcaster")}</span>
        ) : message.moderator ? (
          <span className="rounded border px-1.5 text-xs font-medium">{t("chat.role.moderator")}</span>
        ) : null}
        <time dateTime={iso} className="text-xs text-muted-foreground">
          {formatTime(iso, lang)}
        </time>
      </p>
      <p className="whitespace-pre-wrap break-words leading-snug">{clipText(message.text)}</p>
    </li>
  )
})
