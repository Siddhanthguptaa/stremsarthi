// @vitest-environment jsdom
import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { ApiRequestError, type PublicConfig } from "@/lib/api"
import type { Pairing, SessionView } from "@/lib/api-streamer"
import { chatProblemsFromHealth, chatStatusCode, chatStatusText } from "@/lib/chat-status"
import { bannerKind } from "@/lib/golive-text"
import { parseReport } from "@/lib/report-types"
import { totalEvents } from "@/lib/report-view"
import { ChatStatusNotice } from "@/components/streamer/chat-status-notice"
import { GoLivePanel } from "@/components/streamer/go-live-panel"
import { LiveBanner } from "@/components/streamer/live-banner"
import { renderIn } from "./dom-helpers"
import { reportFixture } from "./report-fixtures"

describe("chat status text", () => {
  it("maps the codes the chat process publishes to EN and HI text", () => {
    for (const code of ["yt_login_required", "twitch_login_required", "twitch_auth_failed", "chat_unavailable", "chat_connectors_failed"]) {
      expect(chatStatusText(code, "en")).toBeTruthy()
      expect(chatStatusText(code, "hi")).toMatch(/[ऀ-ॿ]/)
    }
    expect(chatStatusText("yt_login_required", "en")).toContain("log in to YouTube")
    expect(chatStatusText("made_up", "en")).toBeNull()
  })

  it("picks only chat system_status events", () => {
    const e = { category: "system_status", source: "chat.twitch", detail_code: "twitch_auth_failed" }
    expect(chatStatusCode(e)).toBe("twitch_auth_failed")
    expect(chatStatusCode({ ...e, source: "engine" })).toBeNull()
    expect(chatStatusCode({ ...e, category: "face" })).toBeNull()
    expect(chatStatusCode({ ...e, detail_code: "free text with a name" })).toBeNull()
  })

  it("explains a failed chat process from the health answer", () => {
    expect(chatProblemsFromHealth({ enabled: true, process: "failed", reason: "no_connector", status_codes: [] })).toEqual(["no_connector"])
    expect(chatProblemsFromHealth({ enabled: true, process: "failed", reason: null, status_codes: [] })).toEqual(["chat_failed"])
    expect(chatProblemsFromHealth({ enabled: false, process: "disabled", reason: null, status_codes: [] })).toEqual([])
    expect(chatProblemsFromHealth({ enabled: true, process: "running", reason: null, status_codes: ["yt_login_required", "x"] })).toEqual(["yt_login_required"])
  })

  it("renders the notice, and nothing when there is no problem", () => {
    const { container } = renderIn(<ChatStatusNotice codes={[]} />)
    expect(container.textContent).toBe("")
    renderIn(<ChatStatusNotice codes={["yt_login_required"]} />)
    expect(screen.getByTestId("chat-status").textContent).toContain("log in to YouTube")
  })
})

describe("LIVE banner", () => {
  it("is LIVE only when the engine is live and the session goes to a platform", () => {
    expect(bannerKind({ state: "live", output: "live" })).toBe("live")
    expect(bannerKind({ state: "waiting_ingest", output: "live" })).toBe("waiting")
    expect(bannerKind({ state: "live", output: "rehearsal" })).toBe("rehearsal")
    expect(bannerKind({ state: "ended", output: "live" })).toBe("none")
    expect(bannerKind(null)).toBe("none")
  })

  it("names the platforms", () => {
    renderIn(<LiveBanner session={{ state: "live", output: "live", destinations: ["youtube", "twitch"] }} />)
    expect(screen.getByTestId("live-banner").textContent).toContain("LIVE to YouTube + Twitch")
  })
})

describe("report", () => {
  it("never counts a system notice as a protection event", () => {
    const base = reportFixture() as unknown as { timeline: Record<string, unknown>[]; totals: { events: number } }
    const notice = { ...base.timeline[0], event_id: "n1", category: "system_status", modality: "system", source: "chat", detail_code: "chat_unavailable", bbox: null }
    const noTotals = parseReport({ ...base, timeline: [...base.timeline, notice], totals: undefined })
    expect(noTotals.totals.events).toBe(base.timeline.length)
    expect(totalEvents(noTotals)).toBe(base.timeline.length)
    expect(noTotals.totals.by_category["system_status"]).toBeUndefined()
    const apiTotals = parseReport({ ...base, timeline: [notice], totals: { ...base.totals, events: 0, by_category: {} } })
    expect(apiTotals.totals.events).toBe(0)
  })
})

const config = (): PublicConfig =>
  ({
    profile: "dev",
    delay_seconds: 4,
    categories: {},
    secrets: { youtube_stream_key: null, twitch_stream_key: null },
    destinations: [
      { kind: "youtube", enabled: true },
      { kind: "twitch", enabled: false },
    ],
  }) as PublicConfig

const rehearsal: SessionView = {
  session_id: "rehearsal1",
  state: "live",
  started_at: null,
  delay_seconds: 4,
  profile: "dev",
  hold: { active: false, sources: [] },
  ingest: { srt_url: null, rtmp_url: "rtmp://127.0.0.1:1935/camera?user=a&pass=b" },
  output: "rehearsal",
  destinations: [],
}

describe("go live from the web", () => {
  it("types a key (never shown again), then switches a running rehearsal to live", async () => {
    let configured = false
    const sessionApi = {
      getSession: vi.fn(async () => rehearsal),
      startSession: vi.fn(async () => ({
        session_id: "live1",
        state: "starting",
        login_stable: false,
        checks: { youtube_key: true, twitch_key: false, groq_key: false, sarvam_key: false, gpu: false },
      })),
      stopSession: vi.fn(),
      getPairing: vi.fn(async (): Promise<Pairing> => Promise.reject(new ApiRequestError(404, "not_found"))),
      getPairingQr: vi.fn(async () => new Blob(["png"])),
      listDestinations: vi.fn(async () => ({
        destinations: [
          { kind: "youtube" as const, enabled: true, configured, key_source: configured ? ("session" as const) : null },
          { kind: "twitch" as const, enabled: false, configured: false, key_source: null },
        ],
      })),
      setDestinationKey: vi.fn(async () => {
        configured = true
        return { kind: "youtube" as const, configured: true }
      }),
      clearDestinationKey: vi.fn(),
    }
    renderIn(<GoLivePanel configApi={{ getConfig: async () => config() }} sessionApi={sessionApi} />)
    const input = await screen.findByPlaceholderText("Stream key for YouTube")
    await userEvent.type(input, "abcd-efgh-ijkl-mnop")
    await userEvent.click(screen.getAllByRole("button", { name: "Save key" })[0])
    await waitFor(() => expect(sessionApi.setDestinationKey).toHaveBeenCalledWith("youtube", "abcd-efgh-ijkl-mnop"))
    await waitFor(() => expect(screen.getByTestId("dest-status-youtube").textContent).toContain("key typed here"))
    expect(document.body.textContent).not.toContain("abcd-efgh-ijkl-mnop")
    expect((input as HTMLInputElement).value).toBe("")

    const goLive = await screen.findByRole("button", { name: "Go live now (stops the rehearsal)" })
    await waitFor(() => expect((goLive as HTMLButtonElement).disabled).toBe(false))
    await userEvent.click(goLive)
    await waitFor(() => expect(sessionApi.startSession).toHaveBeenCalledTimes(1))
    expect(sessionApi.startSession).toHaveBeenCalledWith(expect.objectContaining({ replace_rehearsal: true }))
    expect((await screen.findByTestId("new-address")).textContent).toContain("New publish address")
  })
})
