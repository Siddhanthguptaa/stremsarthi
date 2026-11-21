import { expect, test } from "@playwright/test"
import { apiHold, getJson, openAs, scenario, state } from "./helpers"

const holdBtn = (page: import("@playwright/test").Page) => page.locator("button[data-phase]")

test.describe.configure({ mode: "serial" })

test.beforeEach(async ({ request }) => {
  await apiHold(request, false)
})
test.afterAll(async ({ request }) => {
  await apiHold(request, false)
})

test("hold on /live: one tap holds through the API, resume needs the confirmation", async ({ page, request }) => {
  await openAs(page, "streamer", "/live")
  await scenario(request, "engine", { state: "live" })
  const btn = holdBtn(page)
  await expect(btn).toHaveAttribute("data-phase", "released", { timeout: 10_000 })
  const post = page.waitForRequest((r) => r.url().endsWith("/api/hold") && r.method() === "POST")
  await btn.click()
  expect((await post).postDataJSON()).toMatchObject({ active: true })
  await expect(btn).toHaveAttribute("data-phase", "holding")
  expect((await getJson(request, "/api/hold")).body.active ?? (await getJson(request, "/api/hold")).body.held).toBeTruthy()
  // Resume: the click only opens the dialog, nothing is sent until it is confirmed.
  let released = false
  page.on("request", (r) => {
    if (r.url().endsWith("/api/hold") && r.method() === "POST") released = true
  })
  await btn.click()
  const dialog = page.getByRole("dialog")
  await expect(dialog).toContainText("Resume streaming?")
  expect(released).toBe(false)
  await dialog.getByRole("button", { name: "Keep holding" }).click()
  await expect(btn).toHaveAttribute("data-phase", "holding")
  await btn.click()
  await page.getByRole("button", { name: "Resume streaming" }).click()
  await expect(btn).toHaveAttribute("data-phase", "released")
  expect(state().api).toBeTruthy()
})

test("a hold started through the API (round trip) shows on /live from the WebSocket echo", async ({ page, request }) => {
  await openAs(page, "streamer", "/live")
  await expect(holdBtn(page)).toHaveAttribute("data-phase", "released", { timeout: 10_000 })
  const r = await apiHold(request, true)
  expect([200, 202]).toContain(r.status())
  await expect(holdBtn(page)).toHaveAttribute("data-phase", "holding")
  await expect(page.getByTestId("hold-hint")).toContainText(/API/)
  // an engine heartbeat that confirms the hold
  await scenario(request, "engine", { state: "live", hold_active: true, hold_sources: ["api"] })
  await expect(page.getByTestId("hold-hint")).toContainText("Engine confirmed")
  await apiHold(request, false)
  await expect(holdBtn(page)).toHaveAttribute("data-phase", "released")
})

test("the H key toggles hold when focus is not in an input", async ({ page }) => {
  await openAs(page, "streamer", "/live")
  await expect(holdBtn(page)).toHaveAttribute("data-phase", "released", { timeout: 10_000 })
  await page.locator("body").click({ position: { x: 5, y: 5 } })
  await page.keyboard.press("h")
  await expect(holdBtn(page)).toHaveAttribute("data-phase", "holding")
})

test("hold button on /controls works the same way (shared hook)", async ({ page, request }) => {
  await openAs(page, "streamer", "/controls")
  const btn = holdBtn(page)
  await expect(btn).toBeVisible()
  await expect(btn).toHaveAttribute("data-phase", "released", { timeout: 10_000 })
  await btn.click()
  await expect(btn).toHaveAttribute("data-phase", "holding")
  const h = await getJson(request, "/api/hold")
  expect(JSON.stringify(h.body)).toMatch(/app/)
})
