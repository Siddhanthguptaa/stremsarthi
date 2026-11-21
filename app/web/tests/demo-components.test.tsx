// @vitest-environment jsdom
import { act, fireEvent, render, renderHook, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { CaptionStrip } from "@/components/demo/caption-strip"
import { PreviewPane } from "@/components/demo/preview-pane"
import { ProofCounter } from "@/components/demo/proof-counter"
import { LanguageProvider } from "@/components/shell/language-provider"
import { StageStateChip } from "@/components/demo/stage-state"
import { useDemoStatus, usePreviewToken } from "@/components/demo/use-demo-preview"
import { ApiRequestError } from "@/lib/api"
import type { DemoApi, DemoStatus } from "@/lib/demo-api"
import { initialProof, proofReducer } from "@/lib/demo-proof"
import { renderIn } from "./dom-helpers"

const SECRET = "tok-SECRET-value-123"
const URL_1 = `http://api.test/api/demo/preview/protected.mjpg?t=${SECRET}`

describe("PreviewPane", () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it("shows the big label and the stream as a labelled image, never printing the token", () => {
    const { container } = renderIn(<PreviewPane kind="protected" mode="stream" src={URL_1} />)
    expect(screen.getByRole("heading", { level: 2 }).textContent).toContain("WHAT VIEWERS GET")
    const img = screen.getByRole("img")
    expect(img.getAttribute("src")).toBe(URL_1)
    expect(img.getAttribute("alt")).toBe("Protected output: what viewers get, with private information masked")
    expect(img.getAttribute("aria-label")).toBe(img.getAttribute("alt"))
    expect(container.textContent).not.toContain(SECRET)
    // until the first frame arrives there is a status message
    expect(screen.getByText("Connecting...")).toBeTruthy()
    fireEvent.load(img)
    expect(screen.queryByText("Connecting...")).toBeNull()
  })

  it("on an error shows 'stream not available' and retries with a back-off (a fresh image element)", async () => {
    renderIn(<PreviewPane kind="protected" mode="stream" src={URL_1} />)
    const first = screen.getByRole("img")
    fireEvent.error(first)
    expect(screen.queryByRole("img")).toBeNull()
    expect(screen.getByText("Stream not available. Trying again shortly...")).toBeTruthy()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1300) // first retry is about 1 s (+-20 %)
    })
    const second = screen.getByRole("img")
    expect(second).not.toBe(first)
    expect(second.getAttribute("src")).toBe(URL_1)

    // a second error waits longer (about 2 s): nothing yet after 1 s
    fireEvent.error(second)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(screen.queryByRole("img")).toBeNull()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500)
    })
    expect(screen.getByRole("img")).toBeTruthy()
  })

  it("raw preview disabled: explains why and how to enable it, with no image", () => {
    renderIn(<PreviewPane kind="raw" mode="disabled" src={null} />)
    expect(screen.getByRole("heading", { level: 2 }).textContent).toContain("WHAT THE STREAMER SEES")
    const box = screen.getByTestId("raw-disabled")
    expect(box.textContent).toContain("disabled by default for privacy")
    expect(box.textContent).toContain("DEMO_RAW_PREVIEW=1")
    expect(box.textContent).toContain("loopback")
    expect(screen.queryByRole("img")).toBeNull()
  })

  it("waiting, connecting and token-failed placeholders", () => {
    const { unmount } = renderIn(<PreviewPane kind="raw" mode="waiting" src={null} />)
    expect(screen.getByText("Waiting for video. Start a session and send a source.")).toBeTruthy()
    unmount()
    const second = renderIn(<PreviewPane kind="protected" mode="stream" src={null} />)
    expect(screen.getByText("Connecting...")).toBeTruthy()
    second.unmount()
    renderIn(<PreviewPane kind="protected" mode="stream" src={null} tokenFailed />)
    expect(screen.getByText("Stream not available")).toBeTruthy()
  })
})

const URL_NEXT = "http://api.test/api/demo/preview/protected.mjpg?t=NEXT"
const stat = (over: Partial<DemoStatus> = {}): DemoStatus => ({
  raw_enabled: true,
  protected_available: true,
  raw_available: true,
  clients: 0,
  clients_by_stream: {},
  max_clients: 2,
  ...over,
})
const tick = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })

describe("PreviewPane token swap", () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  const view = (src: string) => (
    <LanguageProvider>
      <PreviewPane kind="protected" mode="stream" src={src} />
    </LanguageProvider>
  )
  const nextImg = () => document.querySelector("img[data-slot='next']") as HTMLImageElement | null

  it("keeps the old image on screen until the new one shows its first frame, then swaps", () => {
    const { rerender } = render(view(URL_1))
    const before = screen.getByRole("img")
    fireEvent.load(before)
    rerender(view(URL_NEXT))
    // old image still the visible, labelled one; the new one loads hidden beside it
    expect(screen.getByRole("img")).toBe(before)
    expect(before.getAttribute("src")).toBe(URL_1)
    const incoming = nextImg()
    expect(incoming?.getAttribute("src")).toBe(URL_NEXT)
    expect(incoming?.getAttribute("aria-hidden")).toBe("true")
    expect(screen.queryByText("Connecting...")).toBeNull() // no blank flash

    fireEvent.load(incoming as HTMLImageElement)
    expect(document.querySelectorAll("img")).toHaveLength(1) // the old connection is dropped
    const after = screen.getByRole("img")
    expect(after.getAttribute("src")).toBe(URL_NEXT)
    expect(after.getAttribute("aria-label")).toContain("Protected output")
    expect(screen.queryByText("Connecting...")).toBeNull()
  })

  it("a replacement that never connects is adopted after 12 s (then the normal watchdog and retry apply)", async () => {
    const { rerender } = render(view(URL_1))
    fireEvent.load(screen.getByRole("img"))
    rerender(view(URL_NEXT))
    await tick(11_000)
    expect(screen.getByRole("img").getAttribute("src")).toBe(URL_1)
    await tick(1500)
    expect(screen.getByRole("img").getAttribute("src")).toBe(URL_NEXT)
  })
})

describe("PreviewPane viewer cap (HTTP 503 preview_clients_full)", () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it("asks the status endpoint after an error and, when full, says so and retries every 2 s without backing off", async () => {
    const getStatus = vi.fn(async () => stat({ clients_by_stream: { protected: 2 }, clients: 2 }))
    render(
      <LanguageProvider>
        <PreviewPane kind="protected" mode="stream" src={URL_1} getStatus={getStatus} />
      </LanguageProvider>,
    )
    fireEvent.error(screen.getByRole("img"))
    await tick(0)
    expect(getStatus).toHaveBeenCalledTimes(1)
    expect(screen.getByText("Too many viewers on this preview (2 max). Close other Demo tabs.")).toBeTruthy()
    expect(screen.queryByText(/Trying again shortly/)).toBeNull()

    // three rounds, each exactly 2 s apart (a growing back-off would miss the second and third)
    for (let i = 0; i < 3; i++) {
      await tick(1900)
      expect(screen.queryByRole("img")).toBeNull()
      await tick(200)
      const img = screen.getByRole("img")
      fireEvent.error(img)
      await tick(0)
      expect(screen.getByText(/Too many viewers on this preview \(2 max\)/)).toBeTruthy()
    }
    expect(getStatus).toHaveBeenCalledTimes(4)
  })

  it("not full (or status unreadable): the ordinary 'Stream not available' back-off still applies", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0.5)
    const getStatus = vi.fn(async () => stat({ clients: 0 }))
    render(
      <LanguageProvider>
        <PreviewPane kind="protected" mode="stream" src={URL_1} getStatus={getStatus} />
      </LanguageProvider>,
    )
    fireEvent.error(screen.getByRole("img"))
    await tick(0)
    expect(screen.getByText("Stream not available. Trying again shortly...")).toBeTruthy()
    await tick(1000)
    fireEvent.error(screen.getByRole("img"))
    await tick(1900) // second retry waits 2 s, not 1 s
    expect(screen.queryByRole("img")).toBeNull()
    await tick(200)
    expect(screen.getByRole("img")).toBeTruthy()
    vi.restoreAllMocks()
  })

  it("a failing status call falls back to the ordinary back-off", async () => {
    const getStatus = vi.fn(async () => {
      throw new ApiRequestError(0, "network")
    })
    render(
      <LanguageProvider>
        <PreviewPane kind="protected" mode="stream" src={URL_1} getStatus={getStatus} />
      </LanguageProvider>,
    )
    fireEvent.error(screen.getByRole("img"))
    await tick(0)
    expect(screen.getByText("Stream not available. Trying again shortly...")).toBeTruthy()
  })
})

describe("PreviewPane connecting watchdog", () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  const mount = (getStatus: () => Promise<DemoStatus>) =>
    render(
      <LanguageProvider>
        <PreviewPane kind="protected" mode="stream" src={URL_1} getStatus={getStatus} />
      </LanguageProvider>,
    )

  it("remounts an image stuck on 'Connecting...' for 12 s when the API says the stream is available", async () => {
    const getStatus = vi.fn(async () => stat({ protected_available: true }))
    mount(getStatus)
    const first = screen.getByRole("img")
    await tick(11_000)
    expect(screen.getByRole("img")).toBe(first)
    await tick(1500)
    const second = screen.getByRole("img")
    expect(second).not.toBe(first)
    expect(second.getAttribute("src")).toBe(URL_1)
    expect(screen.getByText("Connecting...")).toBeTruthy()
  })

  it("leaves it alone when the API says there is no stream, or once a frame has arrived", async () => {
    const none = vi.fn(async () => stat({ protected_available: false }))
    const { unmount } = mount(none)
    const img = screen.getByRole("img")
    await tick(13_000)
    expect(screen.getByRole("img")).toBe(img)
    unmount()

    const ok = vi.fn(async () => stat())
    mount(ok)
    const loaded = screen.getByRole("img")
    fireEvent.load(loaded)
    await tick(30_000)
    expect(screen.getByRole("img")).toBe(loaded)
    expect(ok).not.toHaveBeenCalled()
  })
})

function fakeApi(over: Partial<DemoApi> = {}): DemoApi {
  return {
    getPreviewToken: vi.fn(async () => ({ token: "T1", expires_in_s: 900 })),
    getStatus: vi.fn(async () => ({ raw_enabled: false, protected_available: true, raw_available: false, clients: 1, clients_by_stream: {}, max_clients: null })),
    url: (kind, token) => `http://api.test/${kind}?t=${token}`,
    ...over,
  }
}

describe("usePreviewToken", () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it("fetches a token, then replaces it every 4 minutes", async () => {
    let n = 0
    const api = fakeApi({ getPreviewToken: vi.fn(async () => ({ token: `T${++n}`, expires_in_s: 900 })) })
    const { result } = renderHook(() => usePreviewToken(api, true))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.token).toBe("T1")
    await act(async () => {
      await vi.advanceTimersByTimeAsync(239_000)
    })
    expect(result.current.token).toBe("T1")
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(result.current.token).toBe("T2")
    expect(api.getPreviewToken).toHaveBeenCalledTimes(2)
  })

  it("retries with a back-off after a failure and keeps the old token if a refresh fails", async () => {
    let calls = 0
    const api = fakeApi({
      getPreviewToken: vi.fn(async () => {
        calls += 1
        if (calls === 1) throw new ApiRequestError(0, "network")
        if (calls === 3) throw new ApiRequestError(500, "boom")
        return { token: `T${calls}`, expires_in_s: 60 }
      }),
    })
    vi.spyOn(Math, "random").mockReturnValue(0.5) // no jitter: retries at exactly 1 s, 2 s ...
    const { result } = renderHook(() => usePreviewToken(api, true))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current).toEqual({ token: null, failed: true })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1100)
    })
    expect(result.current).toEqual({ token: "T2", failed: false })
    // expires_in 60 s: the refresh 40 s later fails (call 3) and the old token stays in use
    await act(async () => {
      await vi.advanceTimersByTimeAsync(40_500)
    })
    expect(result.current.token).toBe("T2")
    expect(result.current.failed).toBe(true)
  })

  it("does nothing while disabled and stops on unmount", async () => {
    const api = fakeApi()
    const off = renderHook(() => usePreviewToken(api, false))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    expect(api.getPreviewToken).not.toHaveBeenCalled()
    off.unmount()

    const on = renderHook(() => usePreviewToken(api, true))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    on.unmount()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600_000)
    })
    expect(api.getPreviewToken).toHaveBeenCalledTimes(1)
  })

  it("never writes the token to the console", async () => {
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((m) => vi.spyOn(console, m).mockImplementation(() => {}))
    const api = fakeApi()
    renderHook(() => usePreviewToken(api, true))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    for (const spy of spies) expect(spy).not.toHaveBeenCalled()
    spies.forEach((s) => s.mockRestore())
  })
})

describe("useDemoStatus", () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it("polls the status and keeps the last good one when a call fails", async () => {
    let n = 0
    const api = fakeApi({
      getStatus: vi.fn(async () => {
        n += 1
        if (n === 2) throw new ApiRequestError(0, "network")
        return { raw_enabled: false, protected_available: true, raw_available: false, clients: n, clients_by_stream: {}, max_clients: null }
      }),
    })
    const { result } = renderHook(() => useDemoStatus(api, true))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.status?.clients).toBe(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000)
    })
    expect(result.current.failed).toBe(true)
    expect(result.current.status?.clients).toBe(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000)
    })
    expect(result.current).toMatchObject({ failed: false })
    expect(result.current.status?.clients).toBe(3)
  })
})

describe("StageStateChip", () => {
  it("shows LIVE, HOLD and SLATE as text with a hint (never colour alone)", () => {
    const live = renderIn(<StageStateChip kind="live" />)
    expect(screen.getByRole("status").textContent).toContain("LIVE")
    expect(screen.getByRole("status").textContent).toContain("लाइव")
    live.unmount()
    const hold = renderIn(<StageStateChip kind="hold" />)
    expect(screen.getByRole("status").textContent).toContain("HOLD")
    expect(screen.getByTestId("stage-state").getAttribute("data-state")).toBe("hold")
    hold.unmount()
    renderIn(<StageStateChip kind="protect" />)
    expect(screen.getByRole("status").textContent).toContain("SLATE")
    expect(screen.getByText(/cover screen/)).toBeTruthy()
  })

  it("other engine states use the engine wording", () => {
    renderIn(<StageStateChip kind="waiting" />)
    expect(screen.getByRole("status").textContent).toContain("WAITING FOR VIDEO")
  })
})

describe("CaptionStrip", () => {
  it("is empty-state aware, then shows the lines in their language with a badge", () => {
    const empty = renderIn(<CaptionStrip lines={[]} />)
    expect(screen.getByText(/Waiting for speech/)).toBeTruthy()
    empty.unmount()
    renderIn(
      <CaptionStrip
        lines={[
          { id: "1", text: "नमस्ते दोस्तों", lang: "hi", final: true },
          { id: "2", text: "aaj ka stream", lang: "hi", final: false },
        ]}
      />,
    )
    const strip = screen.getByTestId("caption-strip")
    expect(strip.textContent).toContain("नमस्ते दोस्तों")
    expect(strip.textContent).toContain("HI")
    expect(strip.querySelector("[lang='hi']")).not.toBeNull()
    expect(strip.querySelector("[aria-live='off']")).not.toBeNull()
  })
})

describe("ProofCounter", () => {
  it("shows the total and per-category counts, text and numbers only", () => {
    let state = initialProof
    for (const [id, category] of [["1", "aadhaar"], ["2", "aadhaar"], ["3", "face"]] as const) {
      state = proofReducer(state, { type: "event", event: { event_id: id, category, modality: "video", wall_ts: "2026-11-21T09:00:00+00:00" } })
    }
    renderIn(<ProofCounter state={state} />)
    expect(screen.getByTestId("proof-total").textContent).toBe("3")
    const rows = screen.getAllByRole("listitem")
    expect(rows).toHaveLength(2)
    expect(rows[0].textContent).toContain("Aadhaar card")
    expect(rows[0].textContent).toContain("2")
    expect(document.querySelector("img, canvas, video")).toBeNull()
  })

  it("says so when nothing was protected yet", () => {
    renderIn(<ProofCounter state={initialProof} />)
    expect(screen.getByTestId("proof-total").textContent).toBe("0")
    expect(screen.getByText(/Nothing protected yet/)).toBeTruthy()
  })
})
