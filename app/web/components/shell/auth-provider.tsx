"use client"

import * as React from "react"

import { type ApiClient, createApi } from "@/lib/api"
import { bootstrapToken, clearToken, loadStoredToken, subscribeToken, tokenStatusSnapshot } from "@/lib/auth"
import { useLanguage } from "@/components/shell/language-provider"

export type AuthStatus = "loading" | "ready" | "missing"

interface AuthContextValue {
  status: AuthStatus
  api: ApiClient
  getToken: () => string | null
  /** Drop the token (it expired or was rejected) and show the "open the link" screen. */
  signOut: () => void
}

const AuthContext = React.createContext<AuthContextValue | null>(null)

/**
 * Reads the token from the URL fragment once (and removes it from the address bar), then serves it to
 * the API client and the WebSocket. The token lives in memory and sessionStorage only.
 */
export function AuthProvider({ children }: { children: React.ReactNode }) {
  // "loading" on the server and during hydration, then whatever the browser has (no setState in an effect).
  const status = React.useSyncExternalStore<AuthStatus>(subscribeToken, tokenStatusSnapshot, () => "loading")

  React.useEffect(() => {
    bootstrapToken() // consume the URL fragment: store the token, remove it from the address bar
  }, [])

  const signOut = React.useCallback(() => clearToken(), [])

  const api = React.useMemo(() => createApi({ getToken: loadStoredToken, onUnauthorized: signOut }), [signOut])
  const value = React.useMemo<AuthContextValue>(
    () => ({ status, getToken: loadStoredToken, signOut, api }),
    [status, signOut, api],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = React.useContext(AuthContext)
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>")
  return ctx
}

/** Renders its children only with a token; otherwise the "open the link from the terminal" screen. */
export function RequireToken({ children }: { children: React.ReactNode }) {
  const { status } = useAuth()
  const { t } = useLanguage()
  if (status === "ready") return <>{children}</>
  if (status === "loading")
    return (
      <p role="status" className="flex items-center gap-2 text-muted-foreground">
        <span aria-hidden className="led animate-pulse text-primary" />
        {t("auth.loading")}
      </p>
    )
  return (
    <section className="module relative mx-auto max-w-2xl overflow-hidden" aria-labelledby="auth-missing-title">
      {/* A strip of redaction mosaic: the link is the one secret this screen asks for. */}
      <div aria-hidden className="mosaic h-3 w-full" />
      <div className="space-y-5 p-6 md:p-8">
        <div className="flex items-center gap-2 text-sm font-semibold text-muted-foreground">
          <span aria-hidden className="led text-warning" />
          <span>{t("brand.name")}</span>
        </div>
        <h1 id="auth-missing-title" className="text-4xl font-extrabold leading-[1.05] md:text-5xl">
          {t("auth.missing.title")}
        </h1>
        <p className="max-w-prose text-muted-foreground">{t("auth.missing.body")}</p>
        <div className="module-row flex items-center gap-3 border px-4 py-3">
          <span aria-hidden className="font-display text-lg font-bold text-primary">$</span>
          <code className="break-all text-sm">
            http://127.0.0.1:3000/#token=
            <span aria-hidden className="mosaic ml-0.5 inline-block h-3.5 w-16 translate-y-0.5 rounded-sm" />
            <span className="sr-only">…</span>
          </code>
        </div>
      </div>
    </section>
  )
}
