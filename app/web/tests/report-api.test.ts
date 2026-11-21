import { describe, expect, it, vi } from "vitest"

import { createReportClient, latestSessionId, ReportApiError, reportFileName, reportToJson, SESSION_ID_RE } from "@/lib/report-api"
import { parseReport } from "@/lib/report-types"
import { reportFixture } from "./report-fixtures"

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } })
const make = (fetchImpl: typeof fetch, extra: Partial<Parameters<typeof createReportClient>[0]> = {}) =>
  createReportClient({ baseUrl: "http://api.test/", getToken: () => "tok-123", fetchImpl, ...extra })

describe("report client", () => {
  it("GETs /api/report/{id} with the bearer token and returns a sanitised report", async () => {
    const fetchImpl = vi.fn(async () => json(200, reportFixture()))
    const report = await make(fetchImpl as unknown as typeof fetch).getReport("sess-abc_123")
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe("http://api.test/api/report/sess-abc_123")
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok-123")
    expect(init.method).toBe("GET")
    expect(init.credentials).toBe("omit")
    expect(report.timeline).toHaveLength(4)
  })

  it("never sends an id that is not letters, digits, - or _", async () => {
    const fetchImpl = vi.fn()
    const client = make(fetchImpl as unknown as typeof fetch)
    for (const bad of ["../config", "a b", "", "x".repeat(65), "a/b", "a?b=1"]) {
      await expect(client.getReport(bad)).rejects.toMatchObject({ status: 404 })
    }
    expect(fetchImpl).not.toHaveBeenCalled()
    expect(SESSION_ID_RE.test("Ab-_9")).toBe(true)
  })

  it("maps failures to ReportApiError and reports 401 once", async () => {
    const onUnauthorized = vi.fn()
    await expect(make((async () => json(404, { error: "not_found", detail: "" })) as typeof fetch).getReport("s1")).rejects.toMatchObject({ status: 404, code: "not_found" })
    await expect(make((async () => json(403, { error: "forbidden" })) as typeof fetch).getReport("s1")).rejects.toMatchObject({ status: 403 })
    await expect(make((async () => json(401, { error: "unauthorized" })) as typeof fetch, { onUnauthorized }).getReport("s1")).rejects.toBeInstanceOf(ReportApiError)
    expect(onUnauthorized).toHaveBeenCalledTimes(1)
    await expect(make((async () => { throw new TypeError("down") }) as typeof fetch).getReport("s1")).rejects.toMatchObject({ status: 0, code: "network" })
  })

  it("treats a body that is not a report as bad_report", async () => {
    await expect(make((async () => json(200, { hello: "world" })) as typeof fetch).getReport("s1")).rejects.toMatchObject({ status: 502, code: "bad_report" })
  })
})

describe("list and export endpoints", () => {
  it("lists known sessions (oldest first) and picks the latest", async () => {
    const fetchImpl = vi.fn(async () => json(200, { sessions: ["old-1", "bad id!", "new_2"] }))
    const ids = await make(fetchImpl as unknown as typeof fetch).listSessions()
    expect((fetchImpl.mock.calls[0] as unknown as [string])[0]).toBe("http://api.test/api/report")
    expect(ids).toEqual(["old-1", "new_2"])
    expect(latestSessionId(ids)).toBe("new_2")
    expect(latestSessionId([])).toBeNull()
  })

  it("returns 403 for a role that may not read reports", async () => {
    await expect(make((async () => json(403, { error: "forbidden" })) as typeof fetch).listSessions()).rejects.toMatchObject({ status: 403 })
  })

  it("fetches /export?format= with the token and returns the file", async () => {
    const fetchImpl = vi.fn(async () => new Response("<html></html>", { status: 200, headers: { "content-type": "text/html" } }))
    const blob = await make(fetchImpl as unknown as typeof fetch).exportReport("sess-abc_123", "html")
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe("http://api.test/api/report/sess-abc_123/export?format=html")
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok-123")
    expect(await blob.text()).toBe("<html></html>")
    await expect(make(fetchImpl as unknown as typeof fetch).exportReport("../x", "json")).rejects.toMatchObject({ status: 404 })
  })
})

describe("export", () => {
  it("names the file from the safe id and writes only what the sanitiser kept", () => {
    expect(reportFileName("sess-abc_123")).toBe("stream-sarthi-report-sess-abc_123.json")
    expect(reportFileName("../../x")).toBe("stream-sarthi-report-x.json")
    expect(reportFileName("s1", "html")).toBe("stream-sarthi-report-s1.html")
    const dirty = reportFixture()
    ;(dirty as Record<string, unknown>).secret_blob = "should not survive"
    const text = reportToJson(parseReport(dirty))
    expect(text).not.toContain("should not survive")
    expect(JSON.parse(text).schema_version).toBe(1)
  })
})
