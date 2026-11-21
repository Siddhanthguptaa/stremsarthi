"use client"

import * as React from "react"

import { createStreamerApi } from "@/lib/api-streamer"
import { useAuth } from "@/components/shell/auth-provider"

/** The go-live / pairing / risk client, sharing the app's token and sign-out handling. */
export function useStreamerApi() {
  const { getToken, signOut } = useAuth()
  return React.useMemo(() => createStreamerApi({ getToken, onUnauthorized: signOut }), [getToken, signOut])
}
