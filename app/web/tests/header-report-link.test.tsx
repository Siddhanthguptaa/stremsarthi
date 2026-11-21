// @vitest-environment jsdom
import { screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { renderIn } from "./dom-helpers"

const fetchMock = vi.fn()
const getToken = () => "tok"
const auth = { status: "ready" as "ready" | "missing", api: {}, getToken, signOut: () => {} }
vi.mock("@/components/shell/auth-provider", () => ({ useAuth: () => auth }))
vi.mock("next/navigation", () => ({ usePathname: () => "/live" }))

import { AppHeader } from "@/components/shell/app-header"

const respond = (status: number, body: unknown) => new Response(JSON.stringify(body), { status })

beforeEach(() => {
  fetchMock.mockReset()
  auth.status = "ready"
  vi.stubGlobal("fetch", fetchMock)
})

describe("Safety report link in the nav", () => {
  it("links to the latest session's report", async () => {
    fetchMock.mockResolvedValue(respond(200, { sessions: ["old-1", "new_2"] }))
    renderIn(<AppHeader />)
    const link = await screen.findByRole("link", { name: "Safety report" })
    expect(link.getAttribute("href")).toBe("/report/new_2")
    expect(fetchMock.mock.calls[0][0]).toMatch(/\/api\/report$/)
  })

  it("has no link when there is no session yet", async () => {
    fetchMock.mockResolvedValue(respond(200, { sessions: [] }))
    renderIn(<AppHeader />)
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(screen.queryByRole("link", { name: "Safety report" })).toBeNull()
    expect(screen.getByRole("link", { name: "Moderator" })).toBeTruthy()
  })

  it("has no link for a moderator token (403) or when the API is down", async () => {
    fetchMock.mockResolvedValue(respond(403, { error: "forbidden" }))
    renderIn(<AppHeader />)
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(screen.queryByRole("link", { name: "Safety report" })).toBeNull()
  })

  it("does not call the API without a token", () => {
    auth.status = "missing"
    renderIn(<AppHeader />)
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
