// @vitest-environment jsdom
import { screen, within } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { RiskMeter } from "@/components/streamer/risk-meter"
import type { RiskView } from "@/lib/risk"
import { renderIn } from "./dom-helpers"

const view = (over: Partial<RiskView> = {}): RiskView => ({ level: "orange", score: 0.72, signals: [], ...over })

describe("RiskMeter", () => {
  it("is a meter with value, range and a text level", () => {
    renderIn(<RiskMeter view={view()} />)
    const meter = screen.getByRole("meter", { name: "Risk meter" })
    expect(meter.getAttribute("aria-valuenow")).toBe("72")
    expect(meter.getAttribute("aria-valuemin")).toBe("0")
    expect(meter.getAttribute("aria-valuemax")).toBe("100")
    expect(meter.getAttribute("aria-valuetext")).toBe("High, 72%")
    expect(screen.getByText("High")).toBeTruthy()
  })

  it("does not animate (no transition or animation classes)", () => {
    const { container } = renderIn(<RiskMeter view={view({ level: "red", score: 1 })} />)
    expect(container.innerHTML).not.toMatch(/transition|animate-/)
    expect(container.querySelectorAll('[data-filled="true"]')).toHaveLength(4)
  })

  it("lists live warnings by kind, category and score only", () => {
    renderIn(
      <RiskMeter
        view={view({ signals: [{ key: "fingerprint:copyright_match", source: "fingerprint", category: "copyright_match", score: 0.9, expiresAt: 1 }] })}
      />,
    )
    const item = screen.getByRole("listitem")
    expect(within(item).getByText("Copyrighted music match")).toBeTruthy()
    expect(within(item).getByText("Copyright match")).toBeTruthy()
    expect(within(item).getByText("90%")).toBeTruthy()
    expect(document.querySelector("img, canvas, video")).toBeNull()
  })

  it("says so when there are no warnings, and has a Hindi view", () => {
    renderIn(<RiskMeter view={view({ level: "green", score: 0 })} />)
    expect(screen.getByText("No warnings right now.")).toBeTruthy()
    expect(screen.getByText("Low")).toBeTruthy()
  })
})
