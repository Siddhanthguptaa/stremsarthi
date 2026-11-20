"use client"

import * as React from "react"
import { AlertTriangle, CheckCircle2 } from "lucide-react"

import type { ApiClient } from "@/lib/api"
import {
  buildPatch,
  type CategoryChange,
  categoriesInGroup,
  controlsReducer,
  failureReason,
  GROUPS,
  initialControlsState,
  isSaving,
} from "@/lib/controls"
import { RequireToken, useAuth } from "@/components/shell/auth-provider"
import { useLanguage } from "@/components/shell/language-provider"
import { CategoryToggle } from "@/components/streamer/category-toggle"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

/** The /controls screen: needs a token, then shows the panel. */
export function ControlsScreen() {
  return (
    <RequireToken>
      <ControlsWithAuth />
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
  const { t } = useLanguage()
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

  // "Saved" fades after a moment; a revert stays until the next edit so it is not missed.
  React.useEffect(() => {
    if (state.notice?.kind !== "saved") return
    const timer = setTimeout(() => dispatch({ type: "dismiss-notice" }), 2000)
    return () => clearTimeout(timer)
  }, [state.notice])

  const groupLabel = { video: t("controls.group.video"), audio: t("controls.group.audio") } as const
  const groups = GROUPS.map((group) => ({ group, keys: categoriesInGroup(state.categories, group) })).filter(
    (g) => g.keys.length > 0,
  )

  return (
    <section className="space-y-4" aria-labelledby="controls-title">
      <header className="space-y-1">
        <h1 id="controls-title" className="text-2xl font-bold tracking-tight">
          {t("page.controls.title")}
        </h1>
        <p className="text-muted-foreground">{t("page.controls.desc")}</p>
        <p className="text-sm text-muted-foreground">{t("controls.note")}</p>
      </header>

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
        <Tabs defaultValue={groups[0].group}>
          <TabsList aria-label={t("page.controls.title")}>
            {groups.map(({ group }) => (
              <TabsTrigger key={group} value={group}>
                {groupLabel[group]}
              </TabsTrigger>
            ))}
          </TabsList>
          {groups.map(({ group, keys }) => (
            <TabsContent key={group} value={group}>
              <ul className="grid gap-4 md:grid-cols-2">
                {keys.map((key) => (
                  <li key={key}>
                    <CategoryToggle
                      category={key}
                      settings={state.categories[key]}
                      saving={isSaving(state, key)}
                      onChange={edit}
                    />
                  </li>
                ))}
              </ul>
            </TabsContent>
          ))}
        </Tabs>
      ) : null}
    </section>
  )
}
