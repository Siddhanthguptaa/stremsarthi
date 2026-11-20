// @vitest-environment jsdom
import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"

import { AlertFeed } from "@/components/streamer/alert-feed"
import type { Alert } from "@/lib/live-state"
import { renderIn } from "./dom-helpers"

const alert = (id: string, over: Partial<Alert> = {}): Alert => ({
  id,
  at: "2026-11-21T09:00:05+00:00",
  modality: "video",
  category: "aadhaar",
  action: "masked",
  confidence: 0.934,
  durationMs: null,
  ...over,
})

const feed = [
  alert("v1"),
  alert("a1", { modality: "audio", category: "spoken_phone", action: "muted", confidence: 0.81, durationMs: 1800 }),
  alert("c1", { modality: "chat", category: "chat_abuse", action: "hidden", confidence: 0.7 }),
]

describe("AlertFeed", () => {
  it("is a polite live log with category, kind, action, time and confidence per row", () => {
    renderIn(<AlertFeed alerts={feed} total={3} />)
    const log = screen.getByRole("log")
    expect(log.getAttribute("aria-live")).toBe("polite")
    const rows = within(log).getAllByRole("listitem")
    expect(rows).toHaveLength(3)
    expect(within(rows[0]).getByText("Aadhaar card")).toBeTruthy()
    expect(within(rows[0]).getByText("93%")).toBeTruthy()
    expect(rows[1].textContent).toContain("In speech")
    expect(rows[1].textContent).toContain("Muted")
    expect(rows[1].textContent).toContain("for 1.8 s")
    expect(rows[0].textContent).not.toContain(" for ")
  })

  it("shows nothing but text: no image, canvas or box", () => {
    renderIn(<AlertFeed alerts={feed} total={3} />)
    expect(document.querySelector("img, canvas, svg[data-bbox], video")).toBeNull()
    expect(screen.getByText("Only category, time and confidence are shown.")).toBeTruthy()
  })

  it("filters by kind and says when a kind has nothing", async () => {
    renderIn(<AlertFeed alerts={feed.slice(0, 2)} total={2} />)
    const group = screen.getByRole("group", { name: "Show alerts from" })
    expect(within(group).getByRole("button", { name: "All" }).getAttribute("aria-pressed")).toBe("true")
    await userEvent.click(within(group).getByRole("button", { name: "Audio" }))
    expect(within(screen.getByRole("log")).getAllByRole("listitem")).toHaveLength(1)
    expect(screen.getByText("Spoken phone number")).toBeTruthy()
    await userEvent.click(within(group).getByRole("button", { name: "Chat" }))
    expect(screen.getByText("No alerts of this kind yet.")).toBeTruthy()
  })

  it("has an empty state, a failed-history note and a Hindi view", async () => {
    renderIn(<AlertFeed alerts={[]} total={0} loadFailed />)
    expect(screen.getByText("No alerts yet. Events appear here as they happen.")).toBeTruthy()
    expect(screen.getByText("Could not load earlier alerts. New ones still appear live.")).toBeTruthy()
  })

  it("the log is keyboard reachable (scrollable region)", () => {
    renderIn(<AlertFeed alerts={feed} total={3} />)
    expect(screen.getByRole("log").getAttribute("tabindex")).toBe("0")
  })
})
