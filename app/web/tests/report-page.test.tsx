// @vitest-environment jsdom
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { parseReport } from "@/lib/report-types"
import { renderIn } from "./dom-helpers"
import { reportFixture } from "./report-fixtures"

const fetchMock = vi.fn()
const signOut = vi.fn()
const getToken = () => "tok" // stable, like the real provider's (a new function each render would reload forever)
const auth = { status: "ready", api: {}, getToken, signOut }
vi.mock("@/components/shell/auth-provider", () => ({
  useAuth: () => auth,
  RequireToken: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))
const toastMock = vi.hoisted(() => vi.fn())
vi.mock("@/components/ui/use-toast", () => ({ toast: toastMock }))

import { ReportPage, ReportView } from "@/components/report/report-page"

const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } })

beforeEach(() => {
  fetchMock.mockReset()
  signOut.mockReset()
  vi.stubGlobal("fetch", fetchMock)
})

describe("ReportView", () => {
  const report = parseReport(reportFixture())

  it("shows totals, the timeline (chart and table), holds, risk and chat, with category, time and confidence", () => {
    renderIn(<ReportView report={report} />)
    expect(screen.getByRole("heading", { level: 1, name: "Safety report" })).toBeTruthy()
    const tiles = screen.getByTestId("report-tiles").textContent ?? ""
    expect(tiles).toContain("4") // events
    expect(tiles).toContain("30 s") // time on hold
    const cats = within(screen.getByTestId("report-categories"))
    expect(cats.getByText("Face")).toBeTruthy()
    expect(cats.getByText("UPI QR code")).toBeTruthy()
    expect(screen.getByTestId("report-chart").getAttribute("aria-label")).toContain("Face: 2")
    expect(screen.getByTestId("report-chart").querySelectorAll("[data-mark]")).toHaveLength(4)
    const rows = within(screen.getByTestId("report-table")).getAllByRole("row")
    expect(rows).toHaveLength(5) // header + 4 events
    expect(rows[2].textContent).toContain("UPI QR code")
    expect(rows[2].textContent).toContain("97%")
    expect(within(screen.getByTestId("report-holds")).getAllByRole("listitem")).toHaveLength(1)
    expect(screen.getByTestId("report-warnings").textContent).toContain("fp:demo_03")
    expect(screen.getByTestId("report-chat").textContent).toContain("Chat abuse")
    expect(screen.queryByTestId("report-partial")).toBeNull()
  })

  it("has one h1, labelled sections, and no image, canvas or data URL", () => {
    const { container } = renderIn(<ReportView report={report} />)
    expect(container.querySelectorAll("h1")).toHaveLength(1)
    expect(container.querySelector("img, canvas, video, svg image")).toBeNull()
    expect(container.innerHTML).not.toMatch(/data:image|base64/i)
  })

  it("never renders text that is not a category, time, confidence or fixed code", () => {
    const dirty = reportFixture()
    ;(dirty.timeline[0] as Record<string, unknown>).message_text = "SPECIMEN viewer said something"
    const { container } = renderIn(<ReportView report={parseReport(dirty)} />)
    expect(container.textContent).not.toContain("viewer said something")
  })

  it("explains an empty stream and marks a partial report", () => {
    const empty = parseReport(reportFixture({ timeline: [], totals: { by_category: {}, by_action: {}, by_modality: {} }, holds: [], ended_at: null }))
    renderIn(<ReportView report={empty} />)
    expect(screen.getByTestId("report-empty").textContent).toContain("no events were recorded")
    expect(screen.getByTestId("report-partial")).toBeTruthy()
    expect(screen.getByText("No holds during this stream.")).toBeTruthy()
  })

  it("pages a long event list and lists every event for printing", async () => {
    const base = reportFixture().timeline[0]
    const many = Array.from({ length: 250 }, (_, i) => ({ ...base, event_id: `e${i}`, wall_ts: new Date(Date.parse("2026-11-20T10:00:00Z") + i * 1000).toISOString() }))
    renderIn(<ReportView report={parseReport(reportFixture({ timeline: many, totals: undefined }))} />)
    expect(within(screen.getByTestId("report-table")).getAllByRole("row")).toHaveLength(101)
    await userEvent.setup().click(screen.getByRole("button", { name: "Show 100 more" }))
    expect(within(screen.getByTestId("report-table")).getAllByRole("row")).toHaveLength(201)
    act(() => {
      window.dispatchEvent(new Event("beforeprint"))
    })
    expect(within(screen.getByTestId("report-table")).getAllByRole("row")).toHaveLength(251)
  })

  it("shows Hindi labels when the language is Hindi", () => {
    window.localStorage.setItem("ss.lang", "hi")
    renderIn(<ReportView report={report} />)
    expect(screen.getByRole("heading", { level: 1, name: "सुरक्षा रिपोर्ट" })).toBeTruthy()
    expect(within(screen.getByTestId("report-categories")).getByText("चेहरा")).toBeTruthy()
  })

  describe("export", () => {
    const created: { blob: Blob; name: string }[] = []
    let click: ReturnType<typeof vi.spyOn>
    beforeEach(() => {
      created.length = 0
      URL.createObjectURL = vi.fn((b: Blob) => (created.push({ blob: b, name: "" }), "blob:x")) as unknown as typeof URL.createObjectURL
      URL.revokeObjectURL = vi.fn()
      click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
        created[created.length - 1].name = this.download
      })
    })

    it("downloads the server's JSON export as a file", async () => {
      fetchMock.mockResolvedValue(new Response('{"schema_version":1,"session_id":"sess-abc_123"}', { status: 200 }))
      renderIn(<ReportView report={report} />)
      await userEvent.setup().click(screen.getByRole("button", { name: /Download JSON/ }))
      await waitFor(() => expect(click).toHaveBeenCalledTimes(1))
      expect(fetchMock.mock.calls[0][0]).toMatch(/\/api\/report\/sess-abc_123\/export\?format=json$/)
      expect(created[0].name).toBe("stream-sarthi-report-sess-abc_123.json")
      expect(JSON.parse(await created[0].blob.text()).session_id).toBe("sess-abc_123")
    })

    it("downloads the HTML export", async () => {
      fetchMock.mockResolvedValue(new Response("<html></html>", { status: 200 }))
      renderIn(<ReportView report={report} />)
      await userEvent.setup().click(screen.getByRole("button", { name: /printable page/ }))
      await waitFor(() => expect(click).toHaveBeenCalledTimes(1))
      expect(fetchMock.mock.calls[0][0]).toMatch(/format=html$/)
      expect(created[0].name).toBe("stream-sarthi-report-sess-abc_123.html")
    })

    it("saves the cleaned JSON from the page when the server cannot be reached", async () => {
      fetchMock.mockRejectedValue(new TypeError("down"))
      renderIn(<ReportView report={report} />)
      await userEvent.setup().click(screen.getByRole("button", { name: /Download JSON/ }))
      await waitFor(() => expect(click).toHaveBeenCalledTimes(1))
      expect(JSON.parse(await created[0].blob.text()).timeline).toHaveLength(4)
    })

    it("says so, and saves nothing, when the server refuses the download", async () => {
      fetchMock.mockResolvedValue(new Response(JSON.stringify({ error: "not_found" }), { status: 404 }))
      renderIn(<ReportView report={report} />)
      await userEvent.setup().click(screen.getByRole("button", { name: /Download JSON/ }))
      await waitFor(() => expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ variant: "destructive" })))
      expect(click).not.toHaveBeenCalled()
    })

    it("prints", async () => {
      const print = vi.fn()
      vi.stubGlobal("print", print)
      renderIn(<ReportView report={report} />)
      await userEvent.setup().click(screen.getByRole("button", { name: /Print or save as PDF/ }))
      expect(print).toHaveBeenCalledTimes(1)
    })
  })

  it("explains known server notes and shows the live level in words", () => {
    renderIn(<ReportView report={report} />)
    expect(screen.getByTestId("report-notes").textContent).toContain("still live, so this report is partial")
    expect(screen.getByText("Warning")).toBeTruthy() // risk max_level "warn"
  })
})

describe("ReportPage loading", () => {
  it("loads from the API and shows the report", async () => {
    fetchMock.mockResolvedValue(ok(reportFixture()))
    renderIn(<ReportPage sessionId="sess-abc_123" />)
    expect(screen.getByRole("status").textContent).toContain("Loading")
    expect(await screen.findByTestId("report-tiles")).toBeTruthy()
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/api\/report\/sess-abc_123$/)
  })

  it("shows a retry when the API cannot be reached, and loads on retry", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("down")).mockResolvedValueOnce(ok(reportFixture()))
    renderIn(<ReportPage sessionId="s1" />)
    expect((await screen.findByRole("alert")).textContent).toContain("Cannot reach Stream Sarthi")
    fireEvent.click(screen.getByRole("button", { name: "Try again" }))
    expect(await screen.findByTestId("report-tiles")).toBeTruthy()
  })

  it("says the report is gone on a 404, with no retry button", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ error: "not_found" }), { status: 404 }))
    renderIn(<ReportPage sessionId="s1" />)
    expect((await screen.findByRole("alert")).textContent).toContain("No report for this session")
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull()
  })

  it("does not call the API for an invalid session id", async () => {
    renderIn(<ReportPage sessionId="../config" />)
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy())
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it("asks for the link again on a 401", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ error: "unauthorized" }), { status: 401 }))
    renderIn(<ReportPage sessionId="s1" />)
    await screen.findByRole("alert")
    expect(signOut).toHaveBeenCalled()
  })
})
