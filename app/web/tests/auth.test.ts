import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import {
  bootstrapToken,
  clearToken,
  loadStoredToken,
  parseTokenFragment,
  subscribeToken,
  TOKEN_STORAGE_KEY,
  tokenStatusSnapshot,
} from "@/lib/auth"

function fakeStorage() {
  const data = new Map<string, string>()
  return {
    data,
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
  }
}

function fakeWindow(hash: string, over: { blocked?: boolean } = {}) {
  const session = fakeStorage()
  const local = fakeStorage()
  const replaceState = vi.fn()
  const win = {
    location: { hash, pathname: "/live", search: "?a=1" },
    history: { state: { next: "tree" }, replaceState },
    get sessionStorage() {
      if (over.blocked) throw new DOMException("blocked", "SecurityError")
      return session
    },
    localStorage: local,
  }
  vi.stubGlobal("window", win)
  return { win, session, local, replaceState }
}

beforeEach(() => clearToken())
afterEach(() => {
  vi.unstubAllGlobals()
  clearToken()
})

describe("parseTokenFragment", () => {
  it("reads #token=...", () => {
    expect(parseTokenFragment("#token=abc.def")).toBe("abc.def")
    expect(parseTokenFragment("#a=1&token=xyz")).toBe("xyz")
  })
  it("returns null when absent or empty", () => {
    expect(parseTokenFragment("")).toBeNull()
    expect(parseTokenFragment("#")).toBeNull()
    expect(parseTokenFragment("#token=")).toBeNull()
    expect(parseTokenFragment("#tok=abc")).toBeNull()
  })
})

describe("bootstrapToken", () => {
  it("takes the token from the fragment, removes the fragment and keeps router state", () => {
    const { session, local, replaceState } = fakeWindow("#token=SECRET.tok")
    expect(bootstrapToken()).toBe("SECRET.tok")
    expect(replaceState).toHaveBeenCalledWith({ next: "tree" }, "", "/live?a=1") // no hash left
    expect(session.data.get(TOKEN_STORAGE_KEY)).toBe("SECRET.tok")
    expect(local.data.size).toBe(0) // never localStorage
  })

  it("falls back to the stored token on a reload (fragment already gone)", () => {
    const { session } = fakeWindow("")
    session.setItem(TOKEN_STORAGE_KEY, "stored.tok")
    expect(bootstrapToken()).toBe("stored.tok")
  })

  it("returns null with no token anywhere", () => {
    fakeWindow("")
    expect(bootstrapToken()).toBeNull()
    expect(tokenStatusSnapshot()).toBe("missing")
  })

  it("works with sessionStorage blocked (memory only)", () => {
    fakeWindow("#token=mem.tok", { blocked: true })
    expect(bootstrapToken()).toBe("mem.tok")
    expect(loadStoredToken()).toBe("mem.tok")
  })

  it("clearToken forgets it everywhere", () => {
    const { session } = fakeWindow("#token=gone.tok")
    bootstrapToken()
    clearToken()
    expect(loadStoredToken()).toBeNull()
    expect(session.data.size).toBe(0)
  })

  it("tells subscribers when the token appears or goes", () => {
    fakeWindow("#token=live.tok")
    const listener = vi.fn()
    const off = subscribeToken(listener)
    expect(tokenStatusSnapshot()).toBe("ready") // visible before it is consumed
    bootstrapToken()
    expect(listener).toHaveBeenCalled()
    listener.mockClear()
    clearToken()
    expect(listener).toHaveBeenCalledTimes(1)
    off()
    bootstrapToken()
    expect(listener).toHaveBeenCalledTimes(1)
  })
})
