import { describe, expect, it, vi } from "vitest"

import { ApiRequestError } from "@/lib/api"
import { createStreamerApi } from "@/lib/api-streamer"

function fakeFetch(status: number, body: unknown, capture?: { url?: string; init?: RequestInit }) {
  return vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    if (capture) {
      capture.url = String(url)
      capture.init = init
    }
    return new Response(body === undefined ? null : typeof body === "string" ? body : JSON.stringify(body), { status })
  }) as unknown as typeof fetch
}
const make = (fetchImpl: typeof fetch, extra: Partial<Parameters<typeof createStreamerApi>[0]> = {}) =>
  createStreamerApi({ baseUrl: "http://127.0.0.1:8000/", getToken: () => "tok-abc", fetchImpl, ...extra })

describe("session calls", () => {
  it("POSTs the start body with the bearer token, no cookies and no referrer", async () => {
    const seen: { url?: string; init?: RequestInit } = {}
    const api = make(fakeFetch(200, { session_id: "s1", state: "starting", checks: {} }, seen))
    await api.startSession({ destinations: [{ kind: "youtube", enabled: true }], delay_seconds: 4, profile: "dev" })
    expect(seen.url).toBe("http://127.0.0.1:8000/api/session/start")
    expect(seen.init?.method).toBe("POST")
    expect(JSON.parse(String(seen.init?.body))).toEqual({
      destinations: [{ kind: "youtube", enabled: true }],
      delay_seconds: 4,
      profile: "dev",
    })
    expect((seen.init?.headers as Record<string, string>).Authorization).toBe("Bearer tok-abc")
    expect(seen.init?.credentials).toBe("omit")
    expect(seen.init?.referrerPolicy).toBe("no-referrer")
  })
  it("reads the session with GET and stops with POST", async () => {
    const seen: { url?: string; init?: RequestInit } = {}
    await make(fakeFetch(200, { state: "idle" }, seen)).getSession()
    expect(seen.url).toBe("http://127.0.0.1:8000/api/session")
    expect(seen.init?.method).toBe("GET")
    await make(fakeFetch(200, { state: "stopping" }, seen)).stopSession()
    expect(seen.url).toBe("http://127.0.0.1:8000/api/session/stop")
    expect(seen.init?.method).toBe("POST")
  })
  it("turns API errors into ApiRequestError and signals 401", async () => {
    const onUnauthorized = vi.fn()
    const api = make(fakeFetch(409, { error: "session_active", detail: "a session is already running" }), { onUnauthorized })
    await expect(api.stopSession()).rejects.toMatchObject({ status: 409, code: "session_active" })
    const denied = make(fakeFetch(401, { error: "unauthorized" }), { onUnauthorized })
    await expect(denied.getSession()).rejects.toBeInstanceOf(ApiRequestError)
    expect(onUnauthorized).toHaveBeenCalledTimes(1)
  })
  it("reports a network failure as status 0", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError("failed")
    }) as unknown as typeof fetch
    await expect(make(fetchImpl).getRisk()).rejects.toMatchObject({ status: 0, isNetwork: true })
  })
})

describe("pairing and risk", () => {
  it("fetches the QR as a blob with the token (an img src could not send it)", async () => {
    const seen: { url?: string; init?: RequestInit } = {}
    const blob = await make(fakeFetch(200, "PNGDATA", seen)).getPairingQr("rtmp")
    expect(seen.url).toBe("http://127.0.0.1:8000/api/pairing/rtmp.png")
    expect((seen.init?.headers as Record<string, string>).Authorization).toBe("Bearer tok-abc")
    expect(blob.size).toBe(7)
  })
  it("a missing endpoint is a plain 404 the screens can ignore", async () => {
    await expect(make(fakeFetch(404, { error: "not_found" })).getPairing()).rejects.toMatchObject({ status: 404 })
  })
})
