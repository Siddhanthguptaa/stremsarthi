// @vitest-environment jsdom
import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { ApiRequestError, type EnrolList } from "@/lib/api"
import { EnrolPanel } from "@/components/enrollment/enrol-panel"
import { fakeCamera, renderIn } from "./dom-helpers"

const iso = (offsetMs: number) => new Date(Date.now() + offsetMs).toISOString()
const list = (over: Partial<EnrolList> = {}): EnrolList => ({
  faces: [
    { face_id: "f1", label: "Asha Rao", created_at: iso(-3600_000), expires_at: iso(11 * 3600_000 + 30_000) },
    { face_id: "f2", label: "Ravi", created_at: iso(-60_000), expires_at: null },
  ],
  max_faces: 8,
  ttl_s: 43200,
  persisted: false,
  min_frames: 5,
  max_frames: 12,
  ...over,
})

function makeApi(initial: EnrolList = list()) {
  let current = initial
  return {
    getEnrolled: vi.fn(async () => current),
    enrol: vi.fn(async (label: string, frames: Blob[]) => {
      void frames
      const face = { face_id: `f${current.faces.length + 10}`, label, created_at: iso(0), expires_at: iso(43200_000) }
      current = { ...current, faces: [...current.faces, face] }
      return face
    }),
    deleteEnrolled: vi.fn(async (id: string) => {
      current = { ...current, faces: current.faces.filter((f) => f.face_id !== id) }
      return { deleted: true as const }
    }),
  }
}

const scanFlow = async (name: string) => {
  await userEvent.type(screen.getByLabelText("Name or label"), name)
  await userEvent.click(screen.getByRole("button", { name: "Start camera" }))
  await userEvent.click(await screen.findByRole("button", { name: "Scan face" }))
}

describe("EnrolPanel", () => {
  it("lists enrolled people with a generic avatar, an expiry and a remove button, and no picture", async () => {
    const cam = fakeCamera()
    const { container } = renderIn(<EnrolPanel api={makeApi()} captureDeps={cam.deps} />)
    expect(await screen.findByText("Asha Rao")).toBeTruthy()
    expect(screen.getByText("2 of 8")).toBeTruthy()
    const [first, second] = screen.getAllByTestId("expiry")
    expect(first.textContent).toMatch(/Leaves the list in 11 h \d+ min/)
    expect(second.textContent).toBe("Stays until removed")
    expect(screen.getByRole("button", { name: "Remove: Asha Rao" })).toBeTruthy()
    expect(container.innerHTML).not.toMatch(/<img|<canvas|<video|data:image|blob:/)
  })

  it("removes a person with the remove button", async () => {
    const api = makeApi()
    renderIn(<EnrolPanel api={api} captureDeps={fakeCamera().deps} />)
    await userEvent.click(await screen.findByRole("button", { name: "Remove: Asha Rao" }))
    await waitFor(() => expect(screen.queryByText("Asha Rao")).toBeNull())
    expect(api.deleteEnrolled).toHaveBeenCalledWith("f1")
    expect(screen.getByText("Removed from the list")).toBeTruthy()
    expect(screen.getByText("Ravi")).toBeTruthy()
  })

  it("says so when removing fails and keeps the person listed", async () => {
    const api = makeApi()
    api.deleteEnrolled.mockRejectedValueOnce(new ApiRequestError(0, "network"))
    renderIn(<EnrolPanel api={api} captureDeps={fakeCamera().deps} />)
    await userEvent.click(await screen.findByRole("button", { name: "Remove: Ravi" }))
    expect(await screen.findByText("Could not remove")).toBeTruthy()
    expect(screen.getByText("Ravi")).toBeTruthy()
  })

  it("needs a name before scanning", async () => {
    renderIn(<EnrolPanel api={makeApi()} captureDeps={fakeCamera().deps} />)
    await screen.findByText("Asha Rao")
    await userEvent.click(screen.getByRole("button", { name: "Start camera" }))
    const scan = (await screen.findByRole("button", { name: "Scan face" })) as HTMLButtonElement
    expect(scan.disabled).toBe(true)
    expect(screen.getByText("Enter a name before scanning.")).toBeTruthy()
    await userEvent.type(screen.getByLabelText("Name or label"), "  Meera  ")
    await waitFor(() => expect(scan.disabled).toBe(false))
  })

  it("sends the cleaned name and 8 frames, then lists the new person and forgets the name", async () => {
    const api = makeApi()
    const cam = fakeCamera()
    const framesAtCall: number[] = []
    const original = api.enrol.getMockImplementation()!
    api.enrol.mockImplementation(async (label, frames) => {
      framesAtCall.push(frames.length)
      return original(label, frames)
    })
    renderIn(<EnrolPanel api={api} captureDeps={cam.deps} />)
    await screen.findByText("Asha Rao")
    await scanFlow("  Meera   Iyer ")
    expect(await screen.findByText("Face added")).toBeTruthy()
    expect(api.enrol).toHaveBeenCalledTimes(1)
    const [label, frames] = api.enrol.mock.calls[0]
    expect(label).toBe("Meera Iyer")
    expect(framesAtCall).toEqual([8])
    expect(frames).toHaveLength(0) // the capture component emptied its array once the upload settled
    expect(await screen.findByText("Meera Iyer")).toBeTruthy()
    expect((screen.getByLabelText("Name or label") as HTMLInputElement).value).toBe("")
    expect(cam.track.stop).toHaveBeenCalled() // nothing kept: camera off
  })

  it("keeps no label or face data in browser storage", async () => {
    const set = vi.spyOn(Storage.prototype, "setItem")
    renderIn(<EnrolPanel api={makeApi()} captureDeps={fakeCamera().deps} />)
    await screen.findByText("Asha Rao")
    await scanFlow("Meera Iyer")
    await screen.findByText("Face added")
    expect(set).not.toHaveBeenCalled()
    expect(JSON.stringify({ ...window.localStorage, ...window.sessionStorage })).not.toContain("Meera")
  })

  it.each([
    ["too_few_faces", 422, "No clear face in enough frames"],
    ["allowlist_full", 422, "The list is full. Remove someone first."],
    ["enroller_unavailable", 503, "Face enrolment is not ready yet"],
  ])("explains %s without showing the server text", async (code, status, text) => {
    const api = makeApi()
    api.enrol.mockRejectedValueOnce(new ApiRequestError(status, code, "RAW SERVER DETAIL"))
    renderIn(<EnrolPanel api={api} captureDeps={fakeCamera().deps} />)
    await screen.findByText("Asha Rao")
    await scanFlow("Meera")
    await waitFor(() => expect(screen.getByTestId("enrol-message").textContent).toContain(text))
    expect(document.body.textContent).not.toContain("RAW SERVER DETAIL")
  })

  it("disables scanning when the list is full", async () => {
    const faces = Array.from({ length: 8 }, (_, i) => ({
      face_id: `f${i}`,
      label: `Person ${i}`,
      created_at: iso(0),
      expires_at: null,
    }))
    renderIn(<EnrolPanel api={makeApi(list({ faces }))} captureDeps={fakeCamera().deps} />)
    await screen.findByText("Person 7")
    await userEvent.type(screen.getByLabelText("Name or label"), "Another")
    await userEvent.click(screen.getByRole("button", { name: "Start camera" }))
    const scan = (await screen.findByRole("button", { name: "Scan face" })) as HTMLButtonElement
    expect(scan.disabled).toBe(true)
    expect(screen.getByText("The list is full. Remove someone to add another person.")).toBeTruthy()
  })

  it("shows an empty state", async () => {
    renderIn(<EnrolPanel api={makeApi(list({ faces: [] }))} captureDeps={fakeCamera().deps} />)
    expect(await screen.findByText(/Nobody is enrolled yet/)).toBeTruthy()
  })

  it("shows a load error", async () => {
    const failing = makeApi()
    failing.getEnrolled.mockRejectedValue(new ApiRequestError(0, "network"))
    renderIn(<EnrolPanel api={failing} captureDeps={fakeCamera().deps} />)
    expect(await screen.findByText("Could not load the list.")).toBeTruthy()
  })
})
