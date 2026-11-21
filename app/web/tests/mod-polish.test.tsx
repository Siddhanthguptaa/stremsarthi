// @vitest-environment jsdom
import { act, fireEvent, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import type { AnyWsFrame, ReplayMarker, WhoAmI } from "@/lib/wire"
import { renderIn } from "./dom-helpers"
import { chatFrame, modItem } from "./helpers"

type FrameHandler = (frame: AnyWsFrame) => void
const frameHandlers = new Set<FrameHandler>()
const fake = {
  onFrame: (h: FrameHandler) => (frameHandlers.add(h), () => frameHandlers.delete(h)),
  onReplay: (h: (m: ReplayMarker) => void) => (void h, () => {}),
}
const conn = vi.hoisted(() => ({ state: "open" }))
const api = { whoami: vi.fn(), modAction: vi.fn() }

vi.mock("@/components/streamer/use-realtime", () => ({ useRealtime: () => ({ client: fake, state: conn.state }) }))
vi.mock("@/components/shell/auth-provider", () => ({
  useAuth: () => ({ api, status: "ready", getToken: () => "tok", signOut: () => {} }),
  RequireToken: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))
const toast = vi.hoisted(() => vi.fn())
vi.mock("@/components/ui/use-toast", () => ({ toast }))

import { ModConsole } from "@/components/mod/mod-console"

const send = (frame: unknown) => act(() => frameHandlers.forEach((h) => h(frame as AnyWsFrame)))
const queued = (id: string, over: Parameters<typeof modItem>[1] = {}) => send(chatFrame("chat.modqueue", "ModQueueItem", modItem(id, over)))
const asModerator = async () => {
  api.whoami.mockResolvedValue({ role: "moderator", session_id: "s", expires_at: 0 } satisfies WhoAmI)
  renderIn(<ModConsole />)
  await screen.findByRole("heading", { level: 1, name: "Moderator" })
}

beforeEach(() => {
  frameHandlers.clear()
  conn.state = "open"
  toast.mockReset()
  api.whoami.mockReset()
  api.modAction.mockReset().mockResolvedValue({ accepted: true, action: "x" })
})

describe("moderator console polish", () => {
  it("the keys cannot delete or time out a staff message, and say why", async () => {
    await asModerator()
    await queued("s", { message: { is_moderator: true }, verdict: { action: "allow", categories: [] } })
    fireEvent.keyDown(window, { key: "d" })
    fireEvent.keyDown(window, { key: "t" })
    expect(api.modAction).not.toHaveBeenCalled()
    expect(toast).toHaveBeenCalledWith(expect.objectContaining({ description: expect.stringContaining("approved or dismissed") }))
    fireEvent.keyDown(window, { key: "a" }) // approving stays possible
    expect(api.modAction).toHaveBeenCalledWith({ message_id: "s", platform: "twitch", action: "approve" })
  })

  it("moving with J/K puts focus on the selected message", async () => {
    await asModerator()
    await act(async () => {
      send(chatFrame("chat.modqueue", "ModQueueItem", { ...modItem("a"), enqueued_at: 10 }))
      send(chatFrame("chat.modqueue", "ModQueueItem", { ...modItem("b"), enqueued_at: 20 }))
    })
    fireEvent.keyDown(window, { key: "j" })
    expect(document.activeElement?.getAttribute("data-key")).toBe("twitch:a")
    expect(screen.getByRole("status", { name: "" }).textContent).toBeDefined()
  })

  it("shows an offline notice when an action cannot reach the API, and clears it after one that can", async () => {
    const { ApiRequestError } = await import("@/lib/api")
    await asModerator()
    await queued("a")
    api.modAction.mockRejectedValueOnce(new ApiRequestError(0, "network", "down"))
    const user = userEvent.setup()
    await user.click(screen.getByRole("button", { name: "Approve" }))
    expect((await screen.findByTestId("mod-api-down")).getAttribute("role")).toBe("alert")
    await vi.waitFor(() => expect((screen.getByRole("button", { name: "Approve" }) as HTMLButtonElement).disabled).toBe(false))
    await user.click(screen.getByRole("button", { name: "Approve" }))
    await vi.waitFor(() => expect(screen.queryByTestId("mod-api-down")).toBeNull())
  })

  it("a refused (non-network) action does not claim the API is down", async () => {
    const { ApiRequestError } = await import("@/lib/api")
    await asModerator()
    await queued("a")
    api.modAction.mockRejectedValue(new ApiRequestError(409, "not_pending", ""))
    await userEvent.setup().click(screen.getByRole("button", { name: "Approve" }))
    await vi.waitFor(() => expect(toast).toHaveBeenCalled())
    expect(screen.queryByTestId("mod-api-down")).toBeNull()
  })

  it("says the list may be stale while the live link reconnects", async () => {
    conn.state = "reconnecting"
    await asModerator()
    expect(screen.getByTestId("mod-stale")).toBeTruthy()
  })

  it("offers to clear filters when they hide everything", async () => {
    await asModerator()
    await queued("a")
    const user = userEvent.setup()
    await user.click(screen.getByRole("button", { name: "YouTube" }))
    const empty = screen.getByTestId("mod-empty")
    await user.click(within(empty).getByRole("button", { name: "Clear filters" }))
    expect(document.querySelectorAll("[data-key]")).toHaveLength(1)
  })

  it("has a shortcuts list and aria-keyshortcuts on the action buttons", async () => {
    await asModerator()
    await queued("a")
    const shortcuts = screen.getByTestId("mod-shortcuts")
    expect(shortcuts.tagName).toBe("DETAILS")
    expect(screen.getByRole("button", { name: "Approve" }).getAttribute("aria-keyshortcuts")).toBe("A")
    expect(screen.getByRole("button", { name: "Delete" }).getAttribute("aria-keyshortcuts")).toBe("D")
    expect(screen.getByRole("button", { name: /Timeout/ }).getAttribute("aria-keyshortcuts")).toBe("T")
    expect(screen.getByRole("button", { name: "Dismiss" }).getAttribute("aria-keyshortcuts")).toBe("X")
  })

  it("lets a failed access check be retried", async () => {
    api.whoami.mockRejectedValueOnce(new Error("boom")).mockResolvedValueOnce({ role: "moderator", session_id: "s", expires_at: 0 })
    renderIn(<ModConsole />)
    await userEvent.setup().click(await screen.findByRole("button", { name: "Reload" }))
    expect(await screen.findByRole("heading", { level: 1, name: "Moderator" })).toBeTruthy()
    expect(api.whoami).toHaveBeenCalledTimes(2)
  })
})
