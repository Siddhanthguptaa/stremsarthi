// @vitest-environment jsdom
import { act, fireEvent, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type { AnyWsFrame, ReplayMarker, WhoAmI } from "@/lib/wire"
import { renderIn } from "./dom-helpers"
import { chatFrame, chatVerdict, modItem } from "./helpers"

// A controllable stand-in for the realtime client and the auth context.
type FrameHandler = (frame: AnyWsFrame) => void
const frameHandlers = new Set<FrameHandler>()
const replayHandlers = new Set<(m: ReplayMarker) => void>()
const fake = {
  onFrame: (h: FrameHandler) => (frameHandlers.add(h), () => frameHandlers.delete(h)),
  onReplay: (h: (m: ReplayMarker) => void) => (replayHandlers.add(h), () => replayHandlers.delete(h)),
}
const api = { whoami: vi.fn(), modAction: vi.fn() }

vi.mock("@/components/streamer/use-realtime", () => ({ useRealtime: () => ({ client: fake, state: "open" }) }))
vi.mock("@/components/shell/auth-provider", () => ({
  useAuth: () => ({ api, status: "ready", getToken: () => "tok", signOut: () => {} }),
  RequireToken: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

import { ModConsole } from "@/components/mod/mod-console"

const send = (frame: unknown) => act(() => frameHandlers.forEach((h) => h(frame as AnyWsFrame)))
const replay = (m: Partial<ReplayMarker>) =>
  act(() => replayHandlers.forEach((h) => h({ type: "replay", topic: "chat.modqueue", count: 0, hiddenCount: 0, pendingCount: 0, ...m })))
const queued = (id: string, over: Parameters<typeof modItem>[1] = {}) => send(chatFrame("chat.modqueue", "ModQueueItem", modItem(id, over)))
const rows = () => document.querySelectorAll("[data-key]")
const asModerator = async () => {
  api.whoami.mockResolvedValue({ role: "moderator", session_id: "s", expires_at: 0 } satisfies WhoAmI)
  renderIn(<ModConsole />)
  await screen.findByRole("heading", { level: 1, name: "Moderator" })
}

beforeEach(() => {
  frameHandlers.clear()
  replayHandlers.clear()
  api.whoami.mockReset()
  api.modAction.mockReset().mockResolvedValue({ accepted: true, action: "x" })
})

describe("role gate", () => {
  it("shows no queue to a streamer token, and never subscribes", async () => {
    api.whoami.mockResolvedValue({ role: "streamer", session_id: "s", expires_at: 0 })
    renderIn(<ModConsole />)
    expect(await screen.findByRole("heading", { name: "Moderator link needed" })).toBeTruthy()
    expect(screen.queryByTestId("mod-empty")).toBeNull()
    expect(frameHandlers.size).toBe(0)
  })
})

describe("moderator console", () => {
  it("shows an empty state, then a flagged message with text, categories, confidence and four actions", async () => {
    await asModerator()
    expect(screen.getByTestId("mod-empty")).toBeTruthy()
    await queued("a", { message: { text: "synthetic flagged text", author_display: "SPECIMEN viewer" } })
    const item = rows()[0] as HTMLElement
    expect(within(item).getByText("synthetic flagged text")).toBeTruthy()
    expect(within(item).getByText("Chat abuse")).toBeTruthy()
    for (const name of ["Approve", "Delete", "Timeout 10 min", "Dismiss"]) expect(within(item).getByRole("button", { name })).toBeTruthy()
    expect(screen.getByTestId("mod-counts").textContent).toContain("1 waiting")
  })

  it("clears the text when the item is resolved and lists it under recently resolved", async () => {
    await asModerator()
    await queued("a", { message: { text: "secret words" } })
    await queued("a", { state: "deleted" })
    expect(screen.queryByText("secret words")).toBeNull()
    expect(document.body.textContent).not.toContain("secret words")
    expect(within(screen.getByTestId("mod-resolved")).getByText("Deleted")).toBeTruthy()
  })

  it("sends each action with the right body and shows the platform result from the verdict", async () => {
    await asModerator()
    await queued("a")
    const user = userEvent.setup()
    await user.click(screen.getByRole("button", { name: "Approve" }))
    expect(api.modAction).toHaveBeenLastCalledWith({ message_id: "a", platform: "twitch", action: "approve" })
    await send(chatFrame("chat.verdict", "ChatVerdict", chatVerdict("a", { action: "delete", platform_result: "failed" }), { hidden_count: 1 }))
    expect(screen.getByText(/did not accept it/)).toBeTruthy() // failed: the item stays and can be retried
    await user.click(screen.getByRole("button", { name: "1 hour" }))
    await user.click(screen.getByRole("button", { name: "Timeout 1 hour" }))
    expect(api.modAction).toHaveBeenLastCalledWith({ message_id: "a", platform: "twitch", action: "timeout", timeout_seconds: 3600 })
    await send(chatFrame("chat.verdict", "ChatVerdict", chatVerdict("a", { action: "timeout", platform_result: "failed" })))
    await user.click(screen.getByRole("button", { name: "Dismiss" }))
    expect(api.modAction).toHaveBeenLastCalledWith({ message_id: "a", platform: "twitch", action: "dismiss" })
  })

  it("disables a second action while one is running", async () => {
    await asModerator()
    await queued("a")
    api.modAction.mockReturnValue(new Promise(() => {}))
    await userEvent.setup().click(screen.getByRole("button", { name: "Delete" }))
    expect((screen.getByRole("button", { name: "Approve" }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText("Working…")).toBeTruthy()
  })

  it("a staff message can be approved or dismissed but not deleted or timed out", async () => {
    await asModerator()
    await queued("s", { message: { is_moderator: true }, verdict: { action: "allow", categories: [] } })
    expect((screen.getByRole("button", { name: "Delete" }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole("button", { name: /Timeout/ }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole("button", { name: "Approve" }) as HTMLButtonElement).disabled).toBe(false)
  })

  it("a late-connecting console is rebuilt from a replay, dropping what it held before", async () => {
    await asModerator()
    await queued("old", { message: { text: "stale text" } })
    await replay({ count: 1, hiddenCount: 5, pendingCount: 1 })
    expect(document.body.textContent).not.toContain("stale text")
    await queued("fresh")
    expect(rows()).toHaveLength(1)
    expect(screen.getByTestId("mod-counts").textContent).toContain("5 hidden")
  })

  it("filters by platform and category", async () => {
    await asModerator()
    await queued("a")
    await queued("b", { verdict: { categories: ["chat_scam_link"] } })
    const user = userEvent.setup()
    await user.click(screen.getByRole("button", { name: "Chat scam link" }))
    expect(rows()).toHaveLength(1)
    await user.click(screen.getByRole("button", { name: "YouTube" }))
    expect(screen.getByTestId("mod-empty").textContent).toContain("No messages match")
  })

  it("keyboard: J/K move the selection, A D T X act on it, typing is ignored", async () => {
    await asModerator()
    await act(async () => {
      send(chatFrame("chat.modqueue", "ModQueueItem", { ...modItem("a"), enqueued_at: 10 }))
      send(chatFrame("chat.modqueue", "ModQueueItem", { ...modItem("b"), enqueued_at: 20 }))
    })
    const selected = () => document.querySelector('[data-selected="true"]')?.getAttribute("data-key")
    expect(selected()).toBe("twitch:b") // newest first, first selected
    fireEvent.keyDown(window, { key: "j" })
    expect(selected()).toBe("twitch:a")
    fireEvent.keyDown(window, { key: "k" })
    expect(selected()).toBe("twitch:b")
    fireEvent.keyDown(window, { key: "j" })
    fireEvent.keyDown(window, { key: "d" })
    expect(api.modAction).toHaveBeenLastCalledWith({ message_id: "a", platform: "twitch", action: "delete" })
    fireEvent.keyDown(window, { key: "t" }) // a is busy: nothing more is sent
    expect(api.modAction).toHaveBeenCalledTimes(1)
    fireEvent.keyDown(window, { key: "x", ctrlKey: true })
    expect(api.modAction).toHaveBeenCalledTimes(1)
    const input = document.createElement("input")
    document.body.appendChild(input)
    fireEvent.keyDown(input, { key: "a" })
    expect(api.modAction).toHaveBeenCalledTimes(1)
    fireEvent.keyDown(window, { key: "k" })
    fireEvent.keyDown(window, { key: "t" })
    expect(api.modAction).toHaveBeenLastCalledWith({ message_id: "b", platform: "twitch", action: "timeout", timeout_seconds: 600 })
  })

  it("a refused action shows the reason and frees the buttons", async () => {
    const { ApiRequestError } = await import("@/lib/api")
    await asModerator()
    await queued("a")
    api.modAction.mockRejectedValue(new ApiRequestError(409, "not_pending", "already deleted"))
    await userEvent.setup().click(screen.getByRole("button", { name: "Approve" }))
    await vi.waitFor(() => expect((screen.getByRole("button", { name: "Approve" }) as HTMLButtonElement).disabled).toBe(false))
  })

  it("contains no image or canvas", async () => {
    await asModerator()
    await queued("a")
    expect(document.querySelector("img, canvas, video")).toBeNull()
  })
})
