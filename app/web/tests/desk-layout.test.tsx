// @vitest-environment jsdom
import { screen, within } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

import { CaptionStrip } from "@/components/demo/caption-strip"
import { PreviewPane } from "@/components/demo/preview-pane"
import { ProofCounter } from "@/components/demo/proof-counter"
import { ReportView } from "@/components/report/report-page"
import { initialProof, proofReducer } from "@/lib/demo-proof"
import { parseReport } from "@/lib/report-types"
import { renderIn } from "./dom-helpers"
import { reportFixture } from "./report-fixtures"

const getToken = () => "tok"
const auth = { status: "ready", api: {}, getToken, signOut: () => {} }
vi.mock("@/components/shell/auth-provider", () => ({
  useAuth: () => auth,
  RequireToken: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

describe("program monitor", () => {
  it("shows the ON AIR tally and the clock on the protected monitor, never on the raw one", () => {
    const { unmount } = renderIn(<PreviewPane kind="protected" mode="waiting" src={null} tally="onair" timecode="10:20:30" />)
    expect(screen.getByTestId("tally").textContent).toBe("ON AIR")
    expect(screen.getByText("10:20:30")).toBeTruthy()
    expect(document.querySelector("[data-pane='protected']")?.getAttribute("data-tally")).toBe("onair")
    unmount()
    const hold = renderIn(<PreviewPane kind="protected" mode="waiting" src={null} tally="hold" />)
    expect(screen.getByTestId("tally").textContent).toBe("HOLD")
    hold.unmount()
    renderIn(<PreviewPane kind="raw" mode="waiting" src={null} tally="onair" />)
    expect(screen.queryByTestId("tally")).toBeNull()
  })

  it("draws the caption lower-third on the monitor itself", () => {
    renderIn(
      <PreviewPane
        kind="protected"
        mode="waiting"
        src={null}
        overlay={<CaptionStrip lines={[{ id: "1", text: "namaste", lang: "hi", final: true }]} />}
      />,
    )
    const pane = document.querySelector("[data-pane='protected']") as HTMLElement
    expect(within(pane).getByTestId("caption-strip").textContent).toContain("namaste")
  })
})

describe("proof counter by source", () => {
  it("shows one number per source next to the total, as numbers only", () => {
    let state = initialProof
    for (const [id, category, modality] of [["1", "face", "video"], ["2", "face", "video"], ["3", "spoken_phone", "audio"]] as const) {
      state = proofReducer(state, { type: "event", event: { event_id: id, category, modality, wall_ts: "2026-11-21T09:00:00+00:00" } })
    }
    const { container } = renderIn(<ProofCounter state={state} compact />)
    expect(screen.getByTestId("proof-total").textContent).toBe("3")
    expect(container.querySelector("[data-modality='video']")?.textContent).toContain("2")
    expect(container.querySelector("[data-modality='audio']")?.textContent).toContain("1")
    expect(container.querySelector("[data-modality='chat']")?.textContent).toContain("0")
  })
})

describe("report debrief", () => {
  it("draws the whole session as one strip of ticks, one per event, with no image", () => {
    const { container } = renderIn(<ReportView report={parseReport(reportFixture())} />)
    const strip = screen.getByTestId("report-strip")
    expect(strip.querySelectorAll("[data-tick]")).toHaveLength(4)
    expect(strip.querySelectorAll(".mosaic-video")).toHaveLength(3)
    expect(strip.querySelectorAll(".mosaic-audio")).toHaveLength(1)
    expect(container.querySelector("img, canvas, video")).toBeNull()
    const sources = screen.getByTestId("report-sources")
    expect(sources.querySelector("[data-modality='video']")?.textContent).toContain("3")
    expect(sources.querySelector("[data-modality='audio']")?.textContent).toContain("1")
  })
})
