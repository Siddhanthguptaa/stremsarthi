// @vitest-environment jsdom
import { act, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it, vi } from "vitest"

import { CameraCapture } from "@/components/enrollment/camera-capture"
import { deferred, fakeCamera, renderIn } from "./dom-helpers"

const start = () => userEvent.click(screen.getByRole("button", { name: "Start camera" }))

describe("CameraCapture", () => {
  it("shows a permission-denied state with a way to retry, and no video", async () => {
    const cam = fakeCamera()
    cam.deps.getUserMedia.mockRejectedValueOnce(Object.assign(new Error("blocked"), { name: "NotAllowedError" }))
    const { container } = renderIn(<CameraCapture onCaptured={vi.fn()} canScan deps={cam.deps} />)
    await start()
    expect(await screen.findByText("Camera access is blocked")).toBeTruthy()
    expect(screen.getByRole("alert").textContent).toContain("browser settings")
    expect(container.querySelector("video")).toBeNull()

    await userEvent.click(screen.getByRole("button", { name: "Try again" }))
    expect(await screen.findByRole("button", { name: "Scan face" })).toBeTruthy() // second attempt works
    expect(cam.deps.getUserMedia).toHaveBeenCalledTimes(2)
  })

  it("says so when there is no camera", async () => {
    const cam = fakeCamera()
    cam.deps.getUserMedia.mockRejectedValueOnce(Object.assign(new Error("none"), { name: "NotFoundError" }))
    renderIn(<CameraCapture onCaptured={vi.fn()} canScan deps={cam.deps} />)
    await start()
    expect(await screen.findByText("No camera was found on this device.")).toBeTruthy()
  })

  it("asks for the front camera, video only", async () => {
    const cam = fakeCamera()
    renderIn(<CameraCapture onCaptured={vi.fn()} canScan deps={cam.deps} />)
    await start()
    await screen.findByRole("button", { name: "Scan face" })
    expect(cam.deps.getUserMedia).toHaveBeenCalledWith(
      expect.objectContaining({ audio: false, video: expect.objectContaining({ facingMode: "user" }) }),
    )
  })

  it("captures 8 frames, stops the camera and clears the canvas before the upload, then hands the frames over once", async () => {
    const cam = fakeCamera()
    const upload = deferred()
    const onCaptured = vi.fn(async (frames: Blob[]) => {
      // at upload time the camera is already off and the canvas cleared
      expect(cam.track.stop).toHaveBeenCalledTimes(1)
      expect(cam.dispose).toHaveBeenCalledTimes(1)
      expect(frames).toHaveLength(8)
      expect(frames.every((f) => f.type === "image/jpeg")).toBe(true)
      await upload.promise
    })
    const { container } = renderIn(<CameraCapture onCaptured={onCaptured} canScan deps={cam.deps} />)
    await start()
    await userEvent.click(await screen.findByRole("button", { name: "Scan face" }))

    await screen.findByText("Adding face…")
    expect(cam.grab).toHaveBeenCalledTimes(8)
    expect(onCaptured).toHaveBeenCalledTimes(1)
    expect(container.querySelector("video")?.srcObject ?? null).toBeNull()

    await act(async () => upload.resolve())
    await waitFor(() => expect(screen.getByRole("button", { name: "Start camera" })).toBeTruthy())
    expect(container.querySelector("video")).toBeNull()
    expect(container.innerHTML).not.toMatch(/<img|<canvas|data:image|blob:/)
  })

  it("waits between frames so the burst lasts about 3 seconds", async () => {
    const cam = fakeCamera()
    const sleeps: number[] = []
    cam.deps.sleep = async (ms: number) => void sleeps.push(ms)
    renderIn(<CameraCapture onCaptured={async () => {}} canScan deps={cam.deps} />)
    await start()
    await userEvent.click(await screen.findByRole("button", { name: "Scan face" }))
    await waitFor(() => expect(cam.grab).toHaveBeenCalledTimes(8))
    expect(sleeps).toHaveLength(7)
    expect(sleeps.reduce((a, b) => a + b, 0)).toBe(3000)
  })

  it("keeps Scan disabled and explains why until a name is entered", async () => {
    const cam = fakeCamera()
    renderIn(<CameraCapture onCaptured={vi.fn()} canScan={false} blockedHint="Enter a name before scanning." deps={cam.deps} />)
    await start()
    const scan = await screen.findByRole("button", { name: "Scan face" })
    expect((scan as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText("Enter a name before scanning.")).toBeTruthy()
  })

  it("shows live guidance from the preview", async () => {
    const cam = fakeCamera()
    cam.deps.sample.mockResolvedValue({ stats: { luma: 20, sharpness: 20 }, faces: null })
    renderIn(<CameraCapture onCaptured={vi.fn()} canScan deps={cam.deps} />)
    await start()
    expect(await screen.findByText("Too dark. Face a light.", {}, { timeout: 3000 })).toBeTruthy()
  })

  it("turns the camera off when the user stops it", async () => {
    const cam = fakeCamera()
    renderIn(<CameraCapture onCaptured={vi.fn()} canScan deps={cam.deps} />)
    await start()
    await userEvent.click(await screen.findByRole("button", { name: "Stop camera" }))
    expect(cam.track.stop).toHaveBeenCalledTimes(1)
    expect(screen.getByRole("button", { name: "Start camera" })).toBeTruthy()
  })

  it("turns the camera off when the page is left", async () => {
    const cam = fakeCamera()
    const { unmount } = renderIn(<CameraCapture onCaptured={vi.fn()} canScan deps={cam.deps} />)
    await start()
    await screen.findByRole("button", { name: "Scan face" })
    unmount()
    expect(cam.track.stop).toHaveBeenCalled()
  })

  it("drops everything and says so when a frame cannot be read", async () => {
    const cam = fakeCamera()
    cam.grab.mockRejectedValueOnce(new Error("no frame"))
    const onCaptured = vi.fn()
    renderIn(<CameraCapture onCaptured={onCaptured} canScan deps={cam.deps} />)
    await start()
    await userEvent.click(await screen.findByRole("button", { name: "Scan face" }))
    expect(await screen.findByText("Something went wrong. Try again.")).toBeTruthy()
    expect(onCaptured).not.toHaveBeenCalled()
    expect(cam.track.stop).toHaveBeenCalled()
    expect(cam.dispose).toHaveBeenCalled()
  })

  it("never touches browser storage or the console", async () => {
    const cam = fakeCamera()
    const log = vi.spyOn(console, "log")
    const warn = vi.spyOn(console, "warn")
    const set = vi.spyOn(Storage.prototype, "setItem")
    renderIn(<CameraCapture onCaptured={async () => {}} canScan deps={cam.deps} />)
    await start()
    await userEvent.click(await screen.findByRole("button", { name: "Scan face" }))
    await waitFor(() => expect(screen.getByRole("button", { name: "Start camera" })).toBeTruthy())
    expect(set).not.toHaveBeenCalled()
    expect(log).not.toHaveBeenCalled()
    expect(warn).not.toHaveBeenCalled()
  })
})
