import { describe, expect, it } from "vitest"

import {
  initialModState,
  keyAction,
  MOD_TTL_MS,
  type ModEvent,
  modReducer,
  NO_FILTERS,
  pickSelection,
  presentCategories,
  RESOLVED_KEEP,
  visibleItems,
} from "@/lib/mod-state"
import type { ChatVerdict, ModQueueItem } from "@/lib/wire"
import { chatVerdict, modItem } from "./helpers"

const item = (id: string, over: Parameters<typeof modItem>[1] = {}) => modItem(id, over) as unknown as ModQueueItem
const run = (events: ModEvent[]) => events.reduce(modReducer, initialModState)
const put = (id: string, at = 1000, over: Parameters<typeof modItem>[1] = {}): ModEvent => ({ type: "item", item: item(id, over), at })
const key = (e: Pick<KeyboardEvent, "key"> & Partial<KeyboardEvent>) =>
  keyAction({ ctrlKey: false, metaKey: false, altKey: false, target: null, ...e })

describe("moderator queue state", () => {
  it("holds pending items with their text and upserts by platform and id", () => {
    const s = run([put("a"), put("b"), put("a", 2000, { verdict: { confidence: 0.5 } })])
    expect(s.items.map((e) => e.messageId)).toEqual(["a", "b"])
    expect(s.items[0].text).toBe("text of a")
    expect(s.items[0].confidence).toBe(0.5)
    expect(s.items[0].seenAt).toBe(2000)
  })

  it("drops the text the moment an item is resolved and keeps a text-free stub", () => {
    for (const state of ["approved", "deleted", "timed_out", "dismissed"]) {
      const s = run([put("a"), { type: "item", item: item("a", { state }), at: 2000 }])
      expect(s.items).toEqual([])
      expect(s.resolved).toHaveLength(1)
      expect(s.resolved[0].state).toBe(state)
      expect(JSON.stringify(s)).not.toContain("text of a")
      expect(JSON.stringify(s)).not.toContain("viewer one")
    }
  })

  it("keeps only the latest 20 resolved stubs", () => {
    const s = run(Array.from({ length: 30 }, (_, i) => ({ type: "item", item: item(`r${i}`, { state: "dismissed" }), at: 1 }) as ModEvent))
    expect(s.resolved).toHaveLength(RESOLVED_KEEP)
    expect(s.resolved[0].key).toBe("twitch:r29")
  })

  it("takes the platform result from verdicts, before and after the item closes", () => {
    const failed = chatVerdict("a", { action: "delete", platform_result: "failed" }) as unknown as ChatVerdict
    const done = chatVerdict("a", { action: "delete", platform_result: "done" }) as unknown as ChatVerdict
    let s = run([put("a"), { type: "verdict", verdict: failed }])
    expect(s.items[0].platformResult).toBe("failed")
    s = run([put("a"), { type: "verdict", verdict: done }, { type: "item", item: item("a", { state: "deleted" }), at: 3 }])
    expect(s.resolved[0].platformResult).toBe("done") // not overwritten by the item's older copy of the verdict
    s = modReducer(s, { type: "verdict", verdict: failed })
    expect(s.resolved[0].platformResult).toBe("failed")
  })

  it("an unknown verdict changes nothing", () => {
    const s = run([put("a")])
    expect(modReducer(s, { type: "verdict", verdict: chatVerdict("zzz") as unknown as ChatVerdict })).toBe(s)
  })

  it("busy is set by an action and cleared by the answer or the timeout", () => {
    let s = run([put("a"), { type: "busy", key: "twitch:a", action: "delete" }])
    expect(s.items[0].busy).toBe("delete")
    s = modReducer(s, { type: "item", item: item("a"), at: 5 }) // a republish keeps it
    expect(s.items[0].busy).toBe("delete")
    expect(modReducer(s, { type: "busy-clear", key: "twitch:a", action: "approve" })).toBe(s) // a stale timer
    s = modReducer(s, { type: "verdict", verdict: chatVerdict("a", { action: "delete", platform_result: "failed" }) as unknown as ChatVerdict })
    expect(s.items[0].busy).toBeNull()
    expect(modReducer(s, { type: "busy-clear", key: "twitch:a" })).toBe(s)
  })

  it("marks staff messages", () => {
    const s = run([put("a", 1, { message: { is_moderator: true } }), put("b")])
    expect(s.items.map((e) => e.staff)).toEqual([true, false])
  })

  it("expires items after 30 minutes, text and all", () => {
    let s = run([put("old", 1000), put("new", 1000 + MOD_TTL_MS)])
    s = modReducer(s, { type: "expire", now: 1000 + MOD_TTL_MS + 1 })
    expect(s.items.map((e) => e.messageId)).toEqual(["new"])
    expect(JSON.stringify(s)).not.toContain("text of old")
  })

  it("resets on a replay marker, discarding everything it held", () => {
    const s = run([put("a"), { type: "item", item: item("b", { state: "deleted" }), at: 1 }, { type: "reset", hiddenCount: 4 }])
    expect(s).toEqual({ items: [], resolved: [], hiddenCount: 4 })
  })
})

describe("moderator queue view", () => {
  const s = run([
    { type: "item", item: { ...item("a"), enqueued_at: 10 }, at: 1 },
    { type: "item", item: { ...item("b"), enqueued_at: 20 }, at: 1 },
    { type: "item", item: { ...item("c", { verdict: { categories: ["chat_doxxing"], platform: "twitch" } }), enqueued_at: 30 }, at: 1 },
  ])

  it("is newest first and filters by category", () => {
    expect(visibleItems(s.items, NO_FILTERS).map((e) => e.messageId)).toEqual(["c", "b", "a"])
    expect(visibleItems(s.items, { platform: "all", category: "chat_doxxing" }).map((e) => e.messageId)).toEqual(["c"])
    expect(visibleItems(s.items, { platform: "youtube", category: "all" })).toEqual([])
    expect(presentCategories(s.items)).toEqual(["chat_abuse", "chat_doxxing"])
  })

  it("keeps the selection, else the neighbour at the same place", () => {
    const v = visibleItems(s.items, NO_FILTERS)
    expect(pickSelection(v, "twitch:b", 0)).toBe("twitch:b")
    expect(pickSelection(v, "twitch:gone", 1)).toBe("twitch:b")
    expect(pickSelection(v, null, 9)).toBe("twitch:a")
    expect(pickSelection([], "x", 0)).toBeNull()
  })
})

describe("keyboard shortcuts", () => {
  it("maps J K A D T X, ignores modifiers and typing", () => {
    expect(key({ key: "j" })).toEqual({ kind: "move", delta: 1 })
    expect(key({ key: "K" })).toEqual({ kind: "move", delta: -1 })
    expect(key({ key: "a" })).toEqual({ kind: "act", action: "approve" })
    expect(key({ key: "d" })).toEqual({ kind: "act", action: "delete" })
    expect(key({ key: "t" })).toEqual({ kind: "act", action: "timeout" })
    expect(key({ key: "x" })).toEqual({ kind: "act", action: "dismiss" })
    expect(key({ key: "z" })).toBeNull()
    expect(key({ key: "a", ctrlKey: true })).toBeNull()
    expect(key({ key: "d", target: { tagName: "INPUT" } as unknown as EventTarget })).toBeNull()
  })
})
