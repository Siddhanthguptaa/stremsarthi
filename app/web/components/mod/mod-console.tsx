"use client"

import * as React from "react"
import { Keyboard, WifiOff } from "lucide-react"

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
import { modText } from "@/lib/screen-text"
import type { WireTopic, WhoAmI } from "@/lib/wire"
import { RequireToken, useAuth } from "@/components/shell/auth-provider"
import { ConnectionBanner } from "@/components/shell/connection-banner"
import { useLanguage } from "@/components/shell/language-provider"
import { useRealtime } from "@/components/streamer/use-realtime"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card"
import { toast } from "@/components/ui/use-toast"
import { chatStatusCode, withCode } from "@/lib/chat-status"
import { ChatStatusNotice } from "@/components/streamer/chat-status-notice"
import { Filters } from "@/components/mod/filters"
import { QueueList } from "@/components/mod/queue-list"
import { Segmented } from "@/components/mod/segmented"

const MOD_TOPICS: readonly WireTopic[] = ["chat.modqueue", "chat.verdict", "safety.event"]
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
  const { t, lang } = useLanguage()
  const [access, setAccess] = React.useState<Access>("checking")
  const [attempt, setAttempt] = React.useState(0)

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
  }, [api, attempt])

  if (access === "moderator") return <Console />
  if (access === "checking") return <p role="status">{t("mod.role.checking")}</p>
  return (
    <Card>
      <CardHeader>
        <h1 className="text-2xl font-bold leading-tight">{t("mod.role.title")}</h1>
        <CardDescription>{access === "failed" ? t("mod.role.failed") : t("mod.role.body")}</CardDescription>
      </CardHeader>
      <CardContent>
        {access === "failed" ? (
          <Button
            type="button"
            className="min-h-11"
            onClick={() => {
              setAccess("checking")
              setAttempt((n) => n + 1)
            }}
          >
            {modText(lang, "modx.retry")}
          </Button>
        ) : null}
      </CardContent>
    </Card>
  )
}

function Console() {
  const { api, status } = useAuth()
  const { t, lang } = useLanguage()
  const { client, state: connection } = useRealtime(MOD_TOPICS, status === "ready")
  const [state, dispatch] = React.useReducer(modReducer, initialModState)
  const [chatCodes, setChatCodes] = React.useState<string[]>([])
  const [filters, setFilters] = React.useState<ModFilters>(NO_FILTERS)
  const [preset, setPreset] = React.useState<TimeoutPreset>(600)
  // True after an action could not reach the API; cleared by the next one that does.
  const [apiDown, setApiDown] = React.useState(false)
  // The selected message and where it sat in the list: when it leaves, the neighbour at that place is selected.
  const [selection, setSelection] = React.useState<{ key: string | null; index: number }>({ key: null, index: 0 })

  React.useEffect(() => {
    if (!client) return
    const offFrame = client.onFrame((frame) => {
      if (frame.topic === "chat.modqueue") dispatch({ type: "item", item: frame.data.d, at: Date.now() })
      else if (frame.topic === "safety.event") {
        const code = chatStatusCode(frame.data.d)
        if (code) setChatCodes((codes) => withCode(codes, code))
      } else if (frame.topic === "chat.verdict") {
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
      // The buttons are disabled for the streamer's and moderators' own messages; the keys must not get round that.
      if (entry.staff && (action === "delete" || action === "timeout")) {
        toast({ variant: "destructive", title: t("mod.err.title"), description: modText(lang, "modx.staffKey") })
        return
      }
      dispatch({ type: "busy", key: entry.key, action })
      setTimeout(() => dispatch({ type: "busy-clear", key: entry.key, action }), BUSY_MS)
      api
        .modAction({
          message_id: entry.messageId,
          platform: entry.platform,
          action,
          ...(action === "timeout" ? { timeout_seconds: preset } : {}),
        })
        .then(() => setApiDown(false))
        .catch((err: unknown) => {
          dispatch({ type: "busy-clear", key: entry.key })
          setApiDown(err instanceof ApiRequestError && err.isNetwork)
          const code = err instanceof ApiRequestError ? (err.isNetwork ? "network" : err.code) : "generic"
          const key = `mod.err.${code}` as I18nKey
          toast({ variant: "destructive", title: t("mod.err.title"), description: t(translateKey(key)) })
        })
    },
    [api, preset, t, lang],
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
        const node = document.querySelector<HTMLElement>(`[data-key="${CSS.escape(next.key)}"]`)
        node?.scrollIntoView?.({ block: "nearest" })
        node?.focus({ preventScroll: true }) // so a screen reader reads the message that is now selected
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

  const timeoutOptions = TIMEOUT_PRESETS.map((p) => ({ value: p, label: t(`mod.t.${p}` as I18nKey) }))

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div className="min-w-0 space-y-1">
          <h1 className="text-4xl font-extrabold leading-[1.05] md:text-5xl">{t("page.mod.title")}</h1>
          <p className="text-muted-foreground">{t("page.mod.desc")}</p>
        </div>
        <div className="flex flex-wrap gap-2" data-testid="mod-counts">
          <span className="module flex items-center gap-2 px-4 py-2 font-display text-2xl font-bold leading-none">
            <span aria-hidden className="led text-chat" />
            {t("mod.pending").replace("{n}", String(state.items.length))}
          </span>
          <span className="module px-4 py-2 font-display text-2xl font-bold leading-none text-muted-foreground">
            {t("mod.hiddenTotal").replace("{n}", String(state.hiddenCount))}
          </span>
        </div>
      </header>

      {apiDown ? (
        <p role="alert" className="module flex items-start gap-2 border-destructive p-3 text-sm font-medium" data-testid="mod-api-down">
          <WifiOff aria-hidden className="mt-0.5 size-5 shrink-0 text-destructive" />
          {modText(lang, "modx.apiDown")}
        </p>
      ) : null}
      <ConnectionBanner state={connection} apiDown={false} />
      <ChatStatusNotice codes={chatCodes} />
      {connection === "reconnecting" ? (
        <p className="text-sm text-muted-foreground" data-testid="mod-stale">
          {modText(lang, "modx.stale")}
        </p>
      ) : null}

      <div className="grid items-start gap-4 lg:grid-cols-[1.7fr_1fr]">
        <div className="min-w-0 space-y-3">
          <Filters filters={filters} categories={categories} onChange={setFilters} />
          <QueueList
            items={visible}
            filtered={state.items.length > 0}
            selectedKey={selected}
            preset={preset}
            lang={lang}
            onSelect={select}
            onAct={act}
            onClearFilters={() => setFilters(NO_FILTERS)}
          />
          <p role="status" className="sr-only">
            {selected ? modText(lang, "modx.selected", { n: Math.max(1, visible.findIndex((e) => e.key === selected) + 1), total: visible.length }) : ""}
          </p>
        </div>

        <aside className="min-w-0 space-y-4 lg:sticky lg:top-4">
          <section className="module space-y-2 p-4" title={modText(lang, "modx.timeoutHint")}>
            <h2 className="text-xl font-bold">{t("mod.timeoutLength")}</h2>
            <Segmented label={t("mod.timeoutLength")} options={timeoutOptions} value={preset} onChange={setPreset} />
          </section>

          <section aria-labelledby="recent-heading" className="module space-y-3 p-4">
            <h2 id="recent-heading" className="text-xl font-bold">
              {t("mod.recent")}
            </h2>
            {state.resolved.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("mod.recentNone")}</p>
            ) : (
              <ul className="space-y-1.5" data-testid="mod-resolved">
                {state.resolved.map((r) => (
                  <li key={r.key} className="module-row flex flex-wrap items-center gap-x-3 gap-y-0.5 px-3 py-2 text-sm">
                    <span aria-hidden className="led text-chat" />
                    <span className="font-semibold">{t(`mod.state.${r.state}` as I18nKey)}</span>
                    <span className="text-muted-foreground">{r.categories.map((c) => t(categoryKey(c))).join(", ")}</span>
                    {r.platformResult === "failed" || r.platformResult === "unsupported" ? (
                      <span className="text-muted-foreground">{t(`mod.result.${r.platformResult}` as I18nKey)}</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <details open className="module text-sm" data-testid="mod-shortcuts">
            <summary className="flex min-h-11 cursor-pointer items-center gap-2 px-4 font-semibold">
              <Keyboard aria-hidden className="size-4" />
              {modText(lang, "modx.keys.toggle")}
            </summary>
            <div className="space-y-3 border-t px-4 py-3">
              <dl className="grid grid-cols-[auto_1fr] items-center gap-x-4 gap-y-2">
                {(
                  [
                    ["J", "K", "modx.keys.move"],
                    ["A", null, "modx.keys.approve"],
                    ["D", null, "modx.keys.delete"],
                    ["T", null, "modx.keys.timeout"],
                    ["X", null, "modx.keys.dismiss"],
                  ] as const
                ).map(([k, k2, label]) => (
                  <React.Fragment key={k}>
                    <dt className="flex gap-1">
                      <kbd className="keycap min-w-8 rounded-sm bg-desk-raised px-2 py-1 text-center font-mono text-sm font-semibold">{k}</kbd>
                      {k2 ? <kbd className="keycap min-w-8 rounded-sm bg-desk-raised px-2 py-1 text-center font-mono text-sm font-semibold">{k2}</kbd> : null}
                    </dt>
                    <dd>{modText(lang, label)}</dd>
                  </React.Fragment>
                ))}
              </dl>
              <p className="text-xs text-muted-foreground">{modText(lang, "modx.keys.help")}</p>
              <p className="sr-only">{t("mod.keys")}</p>
            </div>
          </details>

          <p className="text-xs text-muted-foreground">{t("mod.privacy")}</p>
        </aside>
      </div>
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
