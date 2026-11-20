import { describe, expect, it } from "vitest"

import { chatOverlayReducer, clipText, initialChatOverlay, MAX_SHOWN_CHARS, OVERLAY_MAX_MESSAGES } from "@/lib/chat-state"
import type { ChatMessage } from "@/lib/wire"
import { chatMessage } from "./helpers"

const msg = (id: string, over: Record<string, unknown> = {}) => chatMessage(id, over) as unknown as ChatMessage

describe("chat overlay state", () => {
  it("shows allowed messages newest first and drops ids it does not display", () => {
    let s = chatOverlayReducer(initialChatOverlay, { type: "allowed", message: msg("a") })
    s = chatOverlayReducer(s, { type: "allowed", message: msg("b") })
    expect(s.messages.map((m) => m.key)).toEqual(["twitch:b", "twitch:a"])
    expect(Object.keys(s.messages[0]).sort()).toEqual(["author", "broadcaster", "key", "moderator", "platform", "receivedAt", "text"])
    expect(JSON.stringify(s)).not.toContain("u1") // author_id
  })

  it("shows a repeated message once (a replay after a reconnect)", () => {
    let s = chatOverlayReducer(initialChatOverlay, { type: "allowed", message: msg("a") })
    s = chatOverlayReducer(s, { type: "allowed", message: msg("a") })
    expect(s.messages).toHaveLength(1)
    expect(s.total).toBe(1)
  })

  it("is capped", () => {
    let s = initialChatOverlay
    for (let i = 0; i < OVERLAY_MAX_MESSAGES + 20; i++) s = chatOverlayReducer(s, { type: "allowed", message: msg(`m${i}`) })
    expect(s.messages).toHaveLength(OVERLAY_MAX_MESSAGES)
    expect(s.messages[0].key).toBe(`twitch:m${OVERLAY_MAX_MESSAGES + 19}`)
  })

  it("follows the hidden count and starts again on a replay marker", () => {
    let s = chatOverlayReducer(initialChatOverlay, { type: "hidden-count", count: 3 })
    s = chatOverlayReducer(s, { type: "allowed", message: msg("a") })
    expect(s.hiddenCount).toBe(3)
    s = chatOverlayReducer(s, { type: "reset", hiddenCount: 1 })
    expect(s).toEqual({ messages: [], hiddenCount: 1, total: 0 })
  })

  it("clips long lines by characters, not UTF-16 units", () => {
    expect(clipText("short")).toBe("short")
    const long = "नमस्ते".repeat(100)
    const clipped = clipText(long)
    expect(Array.from(clipped)).toHaveLength(MAX_SHOWN_CHARS)
    expect(clipped.endsWith("…")).toBe(true)
    expect(clipText("😀".repeat(400)).startsWith("😀😀")).toBe(true)
  })
})
