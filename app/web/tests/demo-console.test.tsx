// @vitest-environment jsdom
import { act, fireEvent, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it, vi } from "vitest"

import { DemoConsole } from "@/components/demo/demo-console"
import { LanguageProvider } from "@/components/shell/language-provider"

const api = {
  getHold: vi.fn(async () => ({ active: false, sources: [] })),
  getStatus: vi.fn(async () => ({ engine: null, engine_age_s: null, hold: { active: false, sources: [] }, bus: { connected: true, contract_ok: true }, session: { state: "idle", session_id: null } })),
  getAlerts: vi.fn(async () => ({ alerts: [], next_cursor: 0, count: 0 })),
  getConfig: vi.fn(async () => ({ categories: {}, captions: { enabled: false, target_lang: "hi" } })),
  patchConfig: vi.fn(),
  hold: vi.fn(),
}
const authValue = { api, status: "ready" as const, getToken: () => "T", signOut: () => {} }

vi.mock("@/components/shell/auth-provider", () => ({
  RequireToken: ({ children }: { children: React.ReactNode }) => children,
  useAuth: () => authValue,
}))
vi.mock("@/components/streamer/use-realtime", () => ({ useRealtime: () => ({ client: null, state: "idle" }) }))
// Stable objects: the console's effects depend on them, so a new object per render would loop.
const streamerApi = {
  getSession: vi.fn(async () => {
    throw new Error("none")
  }),
  getRisk: vi.fn(async () => {
    throw new Error("none")
  }),
}
const demoApi = { getStatus: vi.fn(), url: () => "" }
const demoStatus = {
  status: { raw_enabled: false, protected_available: false, raw_available: false, clients: 2, clients_by_stream: {}, max_clients: null },
  failed: false,
}
const previewToken = { token: null, failed: false }
vi.mock("@/components/streamer/use-streamer-api", () => ({ useStreamerApi: () => streamerApi }))
vi.mock("@/components/demo/use-demo-preview", () => ({
  useDemoApi: () => demoApi,
  useDemoStatus: () => demoStatus,
  usePreviewToken: () => previewToken,
}))

const mount = () =>
  render(
    <LanguageProvider>
      <DemoConsole />
    </LanguageProvider>,
  )
const root = () => screen.getByTestId("demo-console")

afterEach(() => {
  delete document.documentElement.dataset.presenter
})

describe("DemoConsole layout and presenter mode", () => {
  it("puts the two panes first, with the compact risk meter and proof counter in view and the viewers line by the heading", async () => {
    mount()
    await act(async () => {})
    const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent ?? "")
    expect(headings[0]).toContain("What the streamer sees")
    expect(headings[1]).toContain("What viewers get")
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Demo console")
    expect(screen.getByTestId("preview-clients").textContent).toBe("Preview viewers: 2")
    expect(screen.getByRole("meter")).toBeTruthy()
    expect(screen.getByTestId("proof-counter")).toBeTruthy()
    // the stage breaks out of the shell's max-w-5xl column
    expect(root().className).toContain("80rem")
    expect(root().getAttribute("data-presenter")).toBe("off")
    expect(screen.getByRole("region", { name: /hold/i })).toBeTruthy() // the hold bar stays
  })

  it("P toggles presenter mode: navigation hidden, full width, caption strip under the panes, extras gone", async () => {
    mount()
    await act(async () => {})
    const button = screen.getByRole("button", { name: /Presenter mode/ })
    expect(button.getAttribute("aria-pressed")).toBe("false")

    fireEvent.keyDown(window, { key: "p" })
    expect(root().getAttribute("data-presenter")).toBe("on")
    expect(document.documentElement.dataset.presenter).toBe("on")
    expect(root().className).toContain("100vw-2rem")
    expect(root().className).not.toContain("80rem")
    expect(screen.getByTestId("caption-strip")).toBeTruthy()
    expect(screen.queryByRole("meter")).toBeNull()
    expect(screen.queryByTestId("alert-feed")).toBeNull()
    expect(screen.getByTestId("presenter-proof")).toBeTruthy()
    expect(screen.getByRole("region", { name: /hold/i })).toBeTruthy()
    expect(root().querySelector("style")?.textContent).toContain("body > header")

    // a visible, labelled way out as well as the key
    const exit = screen.getByRole("button", { name: /Presenter mode/ })
    expect(exit.getAttribute("aria-pressed")).toBe("true")
    await userEvent.click(exit)
    expect(root().getAttribute("data-presenter")).toBe("off")
    expect(document.documentElement.dataset.presenter).toBeUndefined()
    expect(screen.getByRole("meter")).toBeTruthy()
  })

  it("the P key is ignored while typing and with a modifier", async () => {
    mount()
    await act(async () => {})
    const input = document.createElement("input")
    document.body.appendChild(input)
    fireEvent.keyDown(input, { key: "p" })
    fireEvent.keyDown(window, { key: "p", ctrlKey: true })
    expect(root().getAttribute("data-presenter")).toBe("off")
    input.remove()
  })

  it("keeps the raw pane's explanation and the protected pane while presenting", async () => {
    mount()
    await act(async () => {})
    fireEvent.keyDown(window, { key: "P" })
    expect(screen.getByTestId("raw-disabled").textContent).toContain("DEMO_RAW_PREVIEW=1")
    expect(document.querySelector("[data-pane='protected']")).not.toBeNull()
  })
})
