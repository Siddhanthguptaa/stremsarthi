// @vitest-environment jsdom
import { screen, within } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { ChatOverlay } from "@/components/streamer/chat-overlay"
import { chatOverlayReducer, initialChatOverlay } from "@/lib/chat-state"
import type { ChatMessage } from "@/lib/wire"
import { renderIn } from "./dom-helpers"
import { chatMessage } from "./helpers"

const msg = (id: string, over: Record<string, unknown> = {}) => chatMessage(id, over) as unknown as ChatMessage

describe("ChatOverlay", () => {
  it("lists allowed messages in a polite log with a hidden counter and no hidden text", () => {
    let state = chatOverlayReducer(initialChatOverlay, { type: "allowed", message: msg("a", { text: "hello stream" }) })
    state = chatOverlayReducer(state, { type: "allowed", message: msg("b", { is_broadcaster: true, text: "thanks all" }) })
    state = chatOverlayReducer(state, { type: "hidden-count", count: 3 })
    renderIn(<ChatOverlay state={state} />)
    const log = screen.getByRole("log", { name: "Live chat" })
    expect(log.getAttribute("aria-live")).toBe("polite")
    expect(within(log).getAllByRole("listitem")).toHaveLength(2)
    expect(within(log).getByText("hello stream")).toBeTruthy()
    expect(within(log).getByText("Streamer")).toBeTruthy()
    expect(screen.getByTestId("hidden-count").textContent).toContain("3 hidden")
    expect(screen.getByText(/You do not see them/)).toBeTruthy()
  })

  it("has an empty state and clips very long lines", () => {
    const { unmount } = renderIn(<ChatOverlay state={initialChatOverlay} />)
    expect(screen.getByText(/No chat messages yet/)).toBeTruthy()
    unmount()
    const state = chatOverlayReducer(initialChatOverlay, { type: "allowed", message: msg("a", { text: "x".repeat(900) }) })
    renderIn(<ChatOverlay state={state} />)
    expect(screen.getByRole("log").textContent).toContain("…")
    expect(screen.getByRole("log").textContent?.length).toBeLessThan(500)
  })

  it("renders message text as text, never as markup, and shows no image", () => {
    const state = chatOverlayReducer(initialChatOverlay, { type: "allowed", message: msg("a", { text: '<img src=x onerror=alert(1)>' }) })
    renderIn(<ChatOverlay state={state} />)
    expect(document.querySelector("img")).toBeNull()
    expect(screen.getByRole("log").textContent).toContain("<img src=x")
  })
})
