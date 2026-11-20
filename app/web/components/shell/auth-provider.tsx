"use client"

import * as React from "react"

import { type ApiClient, createApi } from "@/lib/api"
import { bootstrapToken, clearToken, loadStoredToken, subscribeToken, tokenStatusSnapshot } from "@/lib/auth"
import { useLanguage } from "@/components/shell/language-provider"
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card"

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
  if (status === "loading") return <p role="status">{t("auth.loading")}</p>
  return (
    <Card>
      <CardHeader>
        <h1 className="text-2xl font-bold leading-tight">{t("auth.missing.title")}</h1>
        <CardDescription>{t("auth.missing.body")}</CardDescription>
      </CardHeader>
      <CardContent>
        <code className="break-all text-sm">http://127.0.0.1:3000/#token=…</code>
      </CardContent>
    </Card>
  )
}
