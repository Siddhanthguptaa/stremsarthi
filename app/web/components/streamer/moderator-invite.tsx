"use client"

import * as React from "react"
import { Copy, UserPlus } from "lucide-react"

import { ApiRequestError } from "@/lib/api"
import { useAuth } from "@/components/shell/auth-provider"
import { useLanguage } from "@/components/shell/language-provider"
import { Button } from "@/components/ui/button"

/**
 * Streamer only: make a moderator link (`/mod#token=...`). The token goes into the link and nowhere else:
 * it is held in memory while the card is open, never stored, and the moderator's browser strips it from the
 * address bar on arrival.
 */
export function ModeratorInvite() {
  const { api } = useAuth()
  const { t } = useLanguage()
  const [link, setLink] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState(false)
  const [copied, setCopied] = React.useState(false)

  const create = async () => {
    setBusy(true)
    setError(false)
    setCopied(false)
    try {
      const r = await api.modInvite()
      setLink(`${window.location.origin}/mod#token=${r.token}`)
    } catch (err) {
      if (!(err instanceof ApiRequestError)) throw err
      setError(true)
    } finally {
      setBusy(false)
    }
  }

  const copy = async () => {
    if (!link) return
    try {
      await navigator.clipboard.writeText(link)
      setCopied(true)
    } catch {
      /* the field below stays selectable */
    }
  }

  return (
    <section aria-labelledby="invite-heading" className="module space-y-3 p-4">
      <div className="space-y-1">
        <h2 id="invite-heading" className="flex items-center gap-2 text-xl font-bold">
          <UserPlus aria-hidden className="size-5" />
          {t("invite.title")}
        </h2>
        <p className="text-sm text-muted-foreground">{t("invite.desc")}</p>
      </div>
      <div className="space-y-3">
        {link ? (
          <>
            <label className="block space-y-1 text-sm font-medium">
              {t("invite.linkLabel")}
              <input
                readOnly
                value={link}
                onFocus={(e) => e.currentTarget.select()}
                className="block min-h-11 w-full rounded-md border bg-background px-3 font-mono text-xs"
              />
            </label>
            <p className="text-sm text-muted-foreground">{t("invite.warn")}</p>
            <Button type="button" variant="secondary" onClick={copy} className="min-h-11">
              <Copy aria-hidden className="mr-2 size-4" />
              {copied ? t("invite.copied") : t("invite.copy")}
            </Button>
          </>
        ) : (
          <Button type="button" onClick={create} disabled={busy} className="min-h-11">
            {busy ? t("invite.creating") : t("invite.create")}
          </Button>
        )}
        {error ? (
          <p role="alert" className="text-sm font-medium text-destructive">
            {t("invite.failed")}
          </p>
        ) : null}
        <span role="status" className="sr-only">
          {copied ? t("invite.copied") : ""}
        </span>
      </div>
    </section>
  )
}
