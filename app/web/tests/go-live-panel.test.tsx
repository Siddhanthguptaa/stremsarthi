// @vitest-environment jsdom
import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { ApiRequestError, type PublicConfig } from "@/lib/api"
import type { Pairing, SessionView } from "@/lib/api-streamer"
import { GoLivePanel } from "@/components/streamer/go-live-panel"
import { renderIn } from "./dom-helpers"

const config = (over: Record<string, unknown> = {}): PublicConfig =>
  ({
    profile: "dev",
    delay_seconds: 4,
    categories: {},
    secrets: { youtube_stream_key: "***", twitch_stream_key: null },
    destinations: [
      { kind: "youtube", enabled: true },
      { kind: "twitch", enabled: false },
    ],
    ...over,
  }) as PublicConfig

const session = (over: Partial<SessionView> = {}): SessionView => ({
  session_id: null,
  state: "idle",
  started_at: null,
  delay_seconds: 4,
  profile: "dev",
  hold: { active: false, sources: [] },
  ingest: { srt_url: null, rtmp_url: null },
  ...over,
})

function makeApis(cfg = config(), sess = session()) {
  const configApi = { getConfig: vi.fn(async () => cfg) }
  const sessionApi = {
    getSession: vi.fn(async () => sess),
    startSession: vi.fn(async () => ({
      session_id: "abc123",
      state: "starting",
      checks: { youtube_key: true, twitch_key: false, groq_key: true, sarvam_key: false, gpu: false },
    })),
    stopSession: vi.fn(async () => ({ state: "stopping" })),
    getPairing: vi.fn(async (): Promise<Pairing> => Promise.reject(new ApiRequestError(404, "not_found"))),
    getPairingQr: vi.fn(async () => new Blob(["png"])),
  }
  return { configApi, sessionApi }
}

describe("GoLivePanel", () => {
  it("shows each destination with a key status chip and never a key value", async () => {
    renderIn(<GoLivePanel {...makeApis()} />)
    const youtube = await screen.findByRole("switch", { name: "YouTube" })
    expect(youtube.getAttribute("aria-checked")).toBe("true")
    expect(screen.getByRole("switch", { name: "Twitch" }).getAttribute("aria-checked")).toBe("false")
    expect(screen.getByText("Key set")).toBeTruthy()
    expect(screen.getByText("Key missing")).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/\*\*\*/)
  })

  it("starts with the chosen destinations, delay and profile, then shows the setup checks", async () => {
    const apis = makeApis()
    renderIn(<GoLivePanel {...apis} />)
    const start = await screen.findByRole("button", { name: "Start protected stream" })
    await waitFor(() => expect((start as HTMLButtonElement).disabled).toBe(false))
    await userEvent.selectOptions(screen.getByLabelText("Profile"), "demo")
    await userEvent.click(start)
    await waitFor(() => expect(apis.sessionApi.startSession).toHaveBeenCalledTimes(1))
    expect(apis.sessionApi.startSession).toHaveBeenCalledWith({
      destinations: [
        { kind: "youtube", enabled: true },
        { kind: "twitch", enabled: false },
      ],
      delay_seconds: 4,
      profile: "demo",
    })
    const list = await screen.findByTestId("setup-checks")
    expect(within(list).getAllByText("Ready")).toHaveLength(2)
    expect(within(list).getAllByText("Not set (a local fallback is used)")).toHaveLength(3)
  })

  it("keeps Start off and says why when a chosen destination has no key", async () => {
    renderIn(<GoLivePanel {...makeApis()} />)
    await userEvent.click(await screen.findByRole("switch", { name: "Twitch" }))
    const start = screen.getByRole("button", { name: "Start protected stream" }) as HTMLButtonElement
    expect(start.disabled).toBe(true)
    expect(screen.getByTestId("readiness").textContent).toBe("A stream key is missing for: Twitch.")
  })

  it("keeps Start off with no destination", async () => {
    renderIn(<GoLivePanel {...makeApis()} />)
    await userEvent.click(await screen.findByRole("switch", { name: "YouTube" }))
    expect((screen.getByRole("button", { name: "Start protected stream" }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByTestId("readiness").textContent).toBe("Switch on at least one destination.")
  })

  it("shows an API refusal as a message, not a code", async () => {
    const apis = makeApis()
    apis.sessionApi.startSession.mockRejectedValueOnce(new ApiRequestError(409, "session_active"))
    renderIn(<GoLivePanel {...apis} />)
    const start = await screen.findByRole("button", { name: "Start protected stream" })
    await waitFor(() => expect((start as HTMLButtonElement).disabled).toBe(false))
    await userEvent.click(start)
    expect(await screen.findByText("A stream is already running.")).toBeTruthy()
  })

  it("locks delay and profile while live, shows the ingest addresses and stops only after confirming", async () => {
    const live = session({
      state: "live",
      session_id: "abc123",
      delay_seconds: 3.5,
      ingest: { rtmp_url: "rtmp://127.0.0.1:1935/live/r4nd0m", srt_url: "srt://127.0.0.1:8890?streamid=publish:r4nd0m" },
    })
    const apis = makeApis(config(), live)
    apis.sessionApi.getPairing.mockResolvedValue({
      rtmp: { url: "rtmp://127.0.0.1:1935/live/r4nd0m", key: "sessionkey" },
      srt: { url: "srt://127.0.0.1:8890" },
      moblin_url: "moblin://x",
    })
    URL.createObjectURL = vi.fn(() => "blob:qr")
    URL.revokeObjectURL = vi.fn()
    renderIn(<GoLivePanel {...apis} pollMs={50} />)
    expect(await screen.findByText("rtmp://127.0.0.1:1935/live/r4nd0m")).toBeTruthy()
    expect(screen.getByTestId("session-state").textContent).toBe("Live")
    expect(screen.getByLabelText("Profile")).toHaveProperty("disabled", true)
    expect(screen.getByText("The delay cannot change while you are live. Stop the stream to change it.")).toBeTruthy()
    expect(screen.getByText("3.5 s", { selector: "output" })).toBeTruthy()

    // The per-session key is hidden until asked for.
    expect(screen.getByTestId("ingest-key").textContent).toBe("••••••••")
    await userEvent.click(screen.getByRole("button", { name: "Show key" }))
    expect(screen.getByTestId("ingest-key").textContent).toBe("sessionkey")
    expect(await screen.findByAltText("QR code with the Moblin import link")).toBeTruthy()

    await userEvent.click(screen.getByRole("button", { name: "Stop stream" }))
    expect(apis.sessionApi.stopSession).not.toHaveBeenCalled()
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Stop stream" }))
    await waitFor(() => expect(apis.sessionApi.stopSession).toHaveBeenCalledTimes(1))
  })

  it("follows the session state by polling while it runs", async () => {
    const apis = makeApis(config(), session({ state: "starting", session_id: "abc123" }))
    renderIn(<GoLivePanel {...apis} pollMs={20} />)
    expect((await screen.findByTestId("session-state")).textContent).toBe("Starting")
    apis.sessionApi.getSession.mockResolvedValue(session({ state: "live", session_id: "abc123" }))
    await waitFor(() => expect(screen.getByTestId("session-state").textContent).toBe("Live"))
  })

  it("offers a retry when the setup cannot load", async () => {
    const apis = makeApis()
    apis.configApi.getConfig.mockRejectedValueOnce(new Error("down"))
    renderIn(<GoLivePanel {...apis} />)
    expect(await screen.findByText("Could not load your setup.")).toBeTruthy()
    await userEvent.click(screen.getByRole("button", { name: "Try again" }))
    expect(await screen.findByRole("switch", { name: "YouTube" })).toBeTruthy()
  })

  it("renders in Hindi with the same controls", async () => {
    window.localStorage.setItem("ss.lang", "hi")
    renderIn(<GoLivePanel {...makeApis()} />)
    expect(await screen.findByRole("button", { name: "सुरक्षित स्ट्रीम शुरू करें" })).toBeTruthy()
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("लाइव जाएँ")
    window.localStorage.removeItem("ss.lang")
  })

  it("has no image or canvas before a stream starts", async () => {
    renderIn(<GoLivePanel {...makeApis()} />)
    await screen.findByRole("switch", { name: "YouTube" })
    expect(document.querySelector("img, canvas, video")).toBeNull()
    expect(screen.getByText("Addresses appear after you start the stream.")).toBeTruthy()
  })
})
