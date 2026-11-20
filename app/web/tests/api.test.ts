import { describe, expect, it, vi } from "vitest"

import { ApiRequestError, createApi } from "@/lib/api"

function fakeFetch(status: number, body: unknown, capture?: { url?: string; init?: RequestInit }) {
  return vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    if (capture) {
      capture.url = String(url)
      capture.init = init
    }
    return new Response(body === undefined ? null : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    })
  }) as unknown as typeof fetch
}

const make = (fetchImpl: typeof fetch, extra: Partial<Parameters<typeof createApi>[0]> = {}) =>
  createApi({ baseUrl: "http://127.0.0.1:8000/", getToken: () => "tok-abc", fetchImpl, ...extra })

describe("hold", () => {
  it("POSTs {active, source:app} with the bearer token and no credentials or referrer", async () => {
    const seen: { url?: string; init?: RequestInit } = {}
    const api = make(fakeFetch(202, { active: true, sources: ["app"], engine: false, published: 1 }, seen))
    const res = await api.hold(true)
    expect(res).toEqual({ active: true, sources: ["app"], engine: false, published: 1 })
    expect(seen.url).toBe("http://127.0.0.1:8000/api/hold")
    expect(seen.init?.method).toBe("POST")
    expect(JSON.parse(String(seen.init?.body))).toEqual({ active: true, source: "app" })
    const headers = seen.init?.headers as Record<string, string>
    expect(headers.Authorization).toBe("Bearer tok-abc")
    expect(headers["Content-Type"]).toBe("application/json")
    expect(seen.init?.credentials).toBe("omit")
    expect(seen.init?.referrerPolicy).toBe("no-referrer")
    expect(seen.url).not.toContain("tok-abc") // the token is never in a URL
  })

  it("resume asks the API to clear every user source", async () => {
    const seen: { url?: string; init?: RequestInit } = {}
    const api = make(fakeFetch(200, { active: false, sources: [], engine: true, published: 2 }, seen))
    await api.hold(false, { releaseAll: true })
    expect(JSON.parse(String(seen.init?.body))).toEqual({ active: false, source: "app", release_all: true })
  })

  it("sends no Authorization header without a token", async () => {
    const seen: { url?: string; init?: RequestInit } = {}
    const api = createApi({ getToken: () => null, fetchImpl: fakeFetch(200, { active: false, sources: [] }, seen) })
    await api.getHold()
    expect((seen.init?.headers as Record<string, string>).Authorization).toBeUndefined()
  })
})

describe("errors", () => {
  it("turns an error body into ApiRequestError", async () => {
    const api = make(fakeFetch(429, { error: "rate_limited", detail: "too many releases" }))
    await expect(api.hold(false)).rejects.toMatchObject({ status: 429, code: "rate_limited", detail: "too many releases" })
  })

  it("a 401 calls onUnauthorized once and rejects", async () => {
    const onUnauthorized = vi.fn()
    const api = make(fakeFetch(401, { error: "unauthorized", detail: "" }), { onUnauthorized })
    const err = await api.getStatus().catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiRequestError)
    expect((err as ApiRequestError).isUnauthorized).toBe(true)
    expect(onUnauthorized).toHaveBeenCalledTimes(1)
  })

  it("a network failure is status 0 and retryable", async () => {
    const failing = vi.fn(async () => {
      throw new TypeError("Failed to fetch")
    }) as unknown as typeof fetch
    const err = (await make(failing).hold(true).catch((e: unknown) => e)) as ApiRequestError
    expect(err.isNetwork).toBe(true)
    expect(err.status).toBe(0)
  })

  it("aborts a request that takes longer than the timeout", async () => {
    vi.useFakeTimers()
    try {
      const hanging = vi.fn(
        (_url: string | URL | Request, init?: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")))
          }),
      ) as unknown as typeof fetch
      const pending = make(hanging, { timeoutMs: 1500 }).hold(true).catch((e: unknown) => e)
      await vi.advanceTimersByTimeAsync(1600)
      expect(((await pending) as ApiRequestError).isNetwork).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })

  it("an empty or non-JSON error body still gives a typed error", async () => {
    const plain = vi.fn(async () => new Response("Bad Gateway", { status: 502 })) as unknown as typeof fetch
    await expect(make(plain).getHold()).rejects.toMatchObject({ status: 502, code: "http_error" })
  })
})
