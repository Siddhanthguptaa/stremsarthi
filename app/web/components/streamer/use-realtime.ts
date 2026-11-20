"use client"

import * as React from "react"

import { type ConnectionState, RealtimeClient } from "@/lib/ws"
import type { WireTopic } from "@/lib/wire"
import { useAuth } from "@/components/shell/auth-provider"

const noSubscribe = () => () => {}
const idle = (): ConnectionState => "idle"

/**
 * Own one realtime connection for as long as the calling component is mounted. The client is stopped and
 * its handlers are gone on unmount; consumers register frame handlers with `client.onFrame` in an effect.
 */
export function useRealtime(topics: readonly WireTopic[], enabled: boolean) {
  const { getToken, signOut } = useAuth()
  const topicsKey = topics.join(",")
  const client = React.useMemo(
    () => (enabled ? new RealtimeClient({ topics: topicsKey.split(",") as WireTopic[], getToken }) : null),
    [enabled, getToken, topicsKey],
  )

  React.useEffect(() => {
    if (!client) return
    client.start()
    return () => client.stop()
  }, [client])

  const state = React.useSyncExternalStore(
    client ? client.subscribeState : noSubscribe,
    client ? client.getStateSnapshot : idle,
    idle,
  )

  React.useEffect(() => {
    if (state === "unauthorized") signOut() // token expired mid-session: ask for the link again
  }, [state, signOut])

  return { client, state }
}
