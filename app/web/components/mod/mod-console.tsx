"use client"

import * as React from "react"

import { ApiRequestError } from "@/lib/api"
import { categoryKey } from "@/lib/format"
import type { I18nKey } from "@/lib/i18n"
import {
  initialModState,
  keyAction,
  type ModActionKind,
  type ModFilters,
  modReducer,
  NO_FILTERS,
  pickSelection,
  presentCategories,
  type QueueEntry,
  TIMEOUT_PRESETS,
  type TimeoutPreset,
  visibleItems,
} from "@/lib/mod-state"
import type { WireTopic, WhoAmI } from "@/lib/wire"
import { RequireToken, useAuth } from "@/components/shell/auth-provider"
import { ConnectionBanner } from "@/components/shell/connection-banner"
import { useLanguage } from "@/components/shell/language-provider"
import { useRealtime } from "@/components/streamer/use-realtime"
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card"
import { toast } from "@/components/ui/use-toast"
import { Filters } from "@/components/mod/filters"
import { QueueList } from "@/components/mod/queue-list"

const MOD_TOPICS: readonly WireTopic[] = ["chat.modqueue", "chat.verdict"]
/** The API refuses a second action on a message for 10 s; the console frees the buttons after the same time. */
const BUSY_MS = 10_000

/** The moderator console. Only a moderator token gets in: anything else sees how to get a link. */
export function ModConsole() {
  return (
    <RequireToken>
      <RoleGate />
    </RequireToken>
  )
}

type Access = "checking" | "moderator" | "other" | "failed"

function RoleGate() {
  const { api } = useAuth()
  const { t } = useLanguage()
  const [access, setAccess] = React.useState<Access>("checking")

  React.useEffect(() => {
    let live = true
    api
      .whoami()
      .then((me: WhoAmI) => live && setAccess(me.role === "moderator" ? "moderator" : "other"))
      .catch((err: unknown) => {
        if (live && !(err instanceof ApiRequestError && err.isUnauthorized)) setAccess("failed")
      })
    return () => {
      live = false
    }
  }, [api])

  if (access === "moderator") return <Console />
  if (access === "checking") return <p role="status">{t("mod.role.checking")}</p>
  return (
    <Card>
      <CardHeader>
        <h1 className="text-2xl font-bold leading-tight">{t("mod.role.title")}</h1>
        <CardDescription>{access === "failed" ? t("mod.role.failed") : t("mod.role.body")}</CardDescription>
      </CardHeader>
      <CardContent />
    </Card>
  )
}

function Console() {
  const { api, status } = useAuth()
  const { t, lang } = useLanguage()
  const { client, state: connection } = useRealtime(MOD_TOPICS, status === "ready")
  const [state, dispatch] = React.useReducer(modReducer, initialModState)
  const [filters, setFilters] = React.useState<ModFilters>(NO_FILTERS)
  const [preset, setPreset] = React.useState<TimeoutPreset>(600)
  // The selected message and where it sat in the list: when it leaves, the neighbour at that place is selected.
  const [selection, setSelection] = React.useState<{ key: string | null; index: number }>({ key: null, index: 0 })

  React.useEffect(() => {
    if (!client) return
    const offFrame = client.onFrame((frame) => {
      if (frame.topic === "chat.modqueue") dispatch({ type: "item", item: frame.data.d, at: Date.now() })
      else if (frame.topic === "chat.verdict") {
        dispatch({ type: "verdict", verdict: frame.data.d })
        if (frame.meta) dispatch({ type: "hidden-count", count: frame.meta.hidden_count })
      }
    })
    const offReplay = client.onReplay((marker) => {
      if (marker.topic === "chat.modqueue") dispatch({ type: "reset", hiddenCount: marker.hiddenCount })
    })
    return () => {
      offFrame()
      offReplay()
    }
  }, [client])

  // Text must not outlive the server's 30 minute limit.
  React.useEffect(() => {
    const id = setInterval(() => dispatch({ type: "expire", now: Date.now() }), 30_000)
    return () => clearInterval(id)
  }, [])

  const visible = React.useMemo(() => visibleItems(state.items, filters), [state.items, filters])
  const selected = pickSelection(visible, selection.key, selection.index)

  const act = React.useCallback(
    (entry: QueueEntry, action: ModActionKind) => {
      if (entry.busy) return
      dispatch({ type: "busy", key: entry.key, action })
      setTimeout(() => dispatch({ type: "busy-clear", key: entry.key, action }), BUSY_MS)
      api
        .modAction({
          message_id: entry.messageId,
          platform: entry.platform,
          action,
          ...(action === "timeout" ? { timeout_seconds: preset } : {}),
        })
        .catch((err: unknown) => {
          dispatch({ type: "busy-clear", key: entry.key })
          const code = err instanceof ApiRequestError ? (err.isNetwork ? "network" : err.code) : "generic"
          const key = `mod.err.${code}` as I18nKey
          toast({ variant: "destructive", title: t("mod.err.title"), description: t(translateKey(key)) })
        })
    },
    [api, preset, t],
  )

  // Keyboard: J/K move, A/D/T/X act on the selected message.
  const handlers = React.useRef({ visible, selected, act })
  React.useEffect(() => {
    handlers.current = { visible, selected, act }
  })
  React.useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const a = keyAction(event)
      if (!a) return
      const { visible: list, selected: sel, act: run } = handlers.current
      if (list.length === 0) return
      event.preventDefault()
      const at = Math.max(0, list.findIndex((e) => e.key === sel))
      if (a.kind === "move") {
        const index = Math.min(list.length - 1, Math.max(0, at + (a.delta ?? 1)))
        const next = list[index]
        setSelection({ key: next.key, index })
        document.querySelector(`[data-key="${CSS.escape(next.key)}"]`)?.scrollIntoView?.({ block: "nearest" })
      } else if (a.action) {
        run(list[at], a.action)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  const select = React.useCallback((key: string) => {
    const at = handlers.current.visible.findIndex((e) => e.key === key)
    setSelection({ key, index: Math.max(0, at) })
  }, [])

  const categories = React.useMemo(() => presentCategories(state.items), [state.items])

  return (
    <div className="space-y-5">
      <header className="space-y-1">
        <h1 className="text-2xl font-bold tracking-tight">{t("page.mod.title")}</h1>
        <p className="text-muted-foreground">{t("page.mod.desc")}</p>
      </header>

      <ConnectionBanner state={connection} apiDown={false} />

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm font-medium" data-testid="mod-counts">
        <span>{t("mod.pending").replace("{n}", String(state.items.length))}</span>
        <span className="text-muted-foreground">{t("mod.hiddenTotal").replace("{n}", String(state.hiddenCount))}</span>
      </div>

      <Filters filters={filters} categories={categories} onChange={setFilters} />

      <div role="group" aria-label={t("mod.timeoutLength")} className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{t("mod.timeoutLength")}</span>
        {TIMEOUT_PRESETS.map((p) => (
          <button
            key={p}
            type="button"
            aria-pressed={preset === p}
            onClick={() => setPreset(p)}
            className="min-h-11 rounded-full border px-4 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring aria-pressed:border-primary aria-pressed:bg-primary aria-pressed:text-primary-foreground"
          >
            {t(`mod.t.${p}` as I18nKey)}
          </button>
        ))}
      </div>

      <QueueList
        items={visible}
        filtered={state.items.length > 0}
        selectedKey={selected}
        preset={preset}
        lang={lang}
        onSelect={select}
        onAct={act}
      />

      <section aria-labelledby="recent-heading" className="space-y-2">
        <h2 id="recent-heading" className="text-lg font-semibold">
          {t("mod.recent")}
        </h2>
        {state.resolved.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("mod.recentNone")}</p>
        ) : (
          <ul className="space-y-1.5" data-testid="mod-resolved">
            {state.resolved.map((r) => (
              <li key={r.key} className="flex flex-wrap items-center gap-2 rounded-md border bg-card px-3 py-2 text-sm">
                <span className="font-medium">{t(`mod.state.${r.state}` as I18nKey)}</span>
                <span className="text-muted-foreground">{r.categories.map((c) => t(categoryKey(c))).join(", ")}</span>
                {r.platformResult === "failed" || r.platformResult === "unsupported" ? (
                  <span className="text-muted-foreground">{t(`mod.result.${r.platformResult}` as I18nKey)}</span>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>

      <p className="text-xs text-muted-foreground">{t("mod.privacy")}</p>
      <p className="text-xs text-muted-foreground">{t("mod.keys")}</p>
    </div>
  )
}

const KNOWN_ERRORS = new Set([
  "not_found",
  "not_pending",
  "staff_message",
  "busy",
  "rate_limited",
  "bus_unavailable",
  "network",
  "generic",
])
/** An error code we have no sentence for becomes the generic one. */
function translateKey(key: I18nKey): I18nKey {
  return KNOWN_ERRORS.has(key.replace("mod.err.", "")) ? key : "mod.err.generic"
}
