"use client"

import * as React from "react"
import { AlertTriangle, CheckCircle2 } from "lucide-react"

import type { ApiClient } from "@/lib/api"
import {
  buildLanguagesPatch,
  buildPatch,
  type CategoryChange,
  categoriesInGroup,
  controlsReducer,
  failureReason,
  GROUPS,
  initialControlsState,
  isSaving,
} from "@/lib/controls"
import { setupText } from "@/lib/i18n-setup"
import { RequireToken, useAuth } from "@/components/shell/auth-provider"
import { useLanguage } from "@/components/shell/language-provider"
import { CategoryToggle } from "@/components/streamer/category-toggle"
import { HoldBar } from "@/components/streamer/hold-bar"
import { SetupHeader, StatusLed } from "@/components/streamer/setup-header"
import { LanguagePicker } from "@/components/streamer/language-picker"
import { Button } from "@/components/ui/button"

/** The /controls screen: needs a token, then shows the panel. */
export function ControlsScreen() {
  return (
    <RequireToken>
      <ControlsWithAuth />
      <div aria-hidden className="h-52" />
      <HoldBar />
    </RequireToken>
  )
}

function ControlsWithAuth() {
  const { api } = useAuth()
  return <ControlsPanel api={api} />
}

type ControlsApi = Pick<ApiClient, "getConfig" | "patchConfig">

/** Category cards read from and written to `/api/config` (optimistic, rolled back on an error). */
export function ControlsPanel({ api }: { api: ControlsApi }) {
  const { t, lang } = useLanguage()
  const [state, dispatch] = React.useReducer(controlsReducer, initialControlsState)
  const nextId = React.useRef(1)

  const load = React.useCallback(() => {
    const controller = new AbortController()
    api
      .getConfig(controller.signal)
      .then((config) => dispatch({ type: "loaded", config }))
      .catch(() => dispatch({ type: "load-failed" }))
    return () => controller.abort()
  }, [api])

  React.useEffect(() => load(), [load])

  const edit = React.useCallback(
    async (category: string, change: CategoryChange) => {
      const id = nextId.current++
      dispatch({ type: "edit", id, category, change })
      try {
        const config = await api.patchConfig(buildPatch(category, change))
        dispatch({ type: "saved", id, config })
      } catch (error) {
        dispatch({ type: "failed", id, reason: failureReason(error) })
      }
    },
    [api],
  )

  const editLanguages = React.useCallback(
    async (languages: string[]) => {
      const id = nextId.current++
      dispatch({ type: "languages-edit", id, languages })
      try {
        const config = await api.patchConfig(buildLanguagesPatch(languages))
        dispatch({ type: "languages-saved", id, config })
      } catch (error) {
        dispatch({ type: "languages-failed", id, reason: failureReason(error) })
      }
    },
    [api],
  )

  // "Saved" fades after a moment; a revert stays until the next edit so it is not missed.
  React.useEffect(() => {
    if (state.notice?.kind !== "saved") return
    const timer = setTimeout(() => dispatch({ type: "dismiss-notice" }), 2000)
    return () => clearTimeout(timer)
  }, [state.notice])

  const groupLabel = { video: t("controls.group.video"), audio: t("controls.group.audio") } as const
  const languagesSaving = Object.keys(state.languagesInflight).length > 0
  const groups = GROUPS.map((group) => ({ group, keys: categoriesInGroup(state.categories, group) })).filter(
    (g) => g.keys.length > 0,
  )

  const allKeys = groups.flatMap((g) => g.keys)
  const onCount = allKeys.filter((k) => state.categories[k].enabled).length

  return (
    <section className="space-y-6" aria-labelledby="controls-title">
      <SetupHeader id="controls-title" title={t("page.controls.title")} description={t("page.controls.desc")}>
        {state.status === "ready" ? (
          <StatusLed tone="success">{setupText(lang, "setup.controls.count", { on: onCount, all: allKeys.length })}</StatusLed>
        ) : null}
      </SetupHeader>
      <p className="text-sm text-muted-foreground">{t("controls.note")}</p>

      <div role="status" aria-live="polite" className="min-h-6 text-sm" data-testid="controls-notice">
        {state.notice?.kind === "saved" ? (
          <span className="inline-flex items-center gap-1.5 text-success">
            <CheckCircle2 aria-hidden className="size-4" />
            {t("controls.saved")}
          </span>
        ) : null}
        {state.notice?.kind === "reverted" ? (
          <span className="inline-flex items-center gap-1.5 text-destructive">
            <AlertTriangle aria-hidden className="size-4" />
            {state.notice.reason === "restart" ? t("controls.restart") : t("controls.reverted")}
          </span>
        ) : null}
      </div>

      {state.status === "loading" ? <p role="status">{t("controls.loading")}</p> : null}
      {state.status === "error" ? (
        <div className="space-y-2" role="alert">
          <p>{t("controls.loadFailed")}</p>
          <Button variant="outline" size="lg" onClick={() => load()}>
            {t("enrol.retry")}
          </Button>
        </div>
      ) : null}
      {state.status === "ready" && groups.length === 0 ? <p>{t("controls.empty")}</p> : null}

      {groups.length > 0 ? (
        <div className="grid items-start gap-4 lg:grid-cols-[1.5fr_1fr]">
          {groups.map(({ group, keys }) => {
            const on = keys.filter((k) => state.categories[k].enabled).length
            const tone = group === "audio" ? "text-audio" : "text-video"
            return (
              <div key={group} className="space-y-4">
                <section className="module space-y-3 p-4 md:p-5" aria-labelledby={`group-${group}`}>
                  <div className="flex items-center justify-between gap-3">
                    <h2 id={`group-${group}`} className="flex items-center gap-2 font-display text-2xl font-bold">
                      <span aria-hidden className={`led ${tone}`} />
                      {groupLabel[group]}
                    </h2>
                    <span className="text-sm text-muted-foreground">{setupText(lang, "setup.controls.count", { on, all: keys.length })}</span>
                  </div>
                  <ul className="space-y-2">
                    {keys.map((key) => (
                      <li key={key}>
                        <CategoryToggle category={key} settings={state.categories[key]} saving={isSaving(state, key)} onChange={edit} />
                      </li>
                    ))}
                  </ul>
                </section>
                {group === "audio" && state.languages.length > 0 ? (
                  <LanguagePicker
                    selected={state.languages}
                    supported={state.supportedLanguages}
                    saving={languagesSaving}
                    onChange={editLanguages}
                  />
                ) : null}
              </div>
            )
          })}
        </div>
      ) : null}
    </section>
  )
}
