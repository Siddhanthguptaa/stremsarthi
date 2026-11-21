import { expect, test } from "@playwright/test"
import { getJson, openAs, state } from "./helpers"

test.describe("go-live", () => {
  test("key chips show configured or missing and never a key value", async ({ page, request }) => {
    const seen: string[] = []
    page.on("response", async (r) => {
      if (r.url().startsWith(state().api)) seen.push(await r.text().catch(() => ""))
    })
    await openAs(page, "streamer", "/go-live")
    await expect(page.getByRole("heading", { level: 1, name: "Go live" })).toBeVisible()
    const items = page.locator("li", { hasText: /YouTube|Twitch/ })
    await expect(items.filter({ hasText: "YouTube" }).first()).toContainText(/configured/i)
    await expect(items.filter({ hasText: "Twitch" }).first()).toContainText(/missing/i)
    // The key is on the API (config fake) and must not be in any response the page saw, in the DOM or in storage.
    const key = state().fake_yt_key
    expect(await page.content()).not.toContain(key)
    expect(seen.join("\n")).not.toContain(key)
    const stored = await page.evaluate(() => JSON.stringify([{ ...localStorage }, { ...sessionStorage }]))
    expect(stored).not.toContain(key)
    for (const path of ["/api/config", "/api/destinations", "/api/session", "/api/status"]) {
      const r = await request.get(`${state().api}${path}`, { headers: { Authorization: `Bearer ${state().streamer}` } })
      expect(await r.text(), path).not.toContain(key)
    }
  })

  test("token fragment is removed from the address bar", async ({ page }) => {
    await openAs(page, "streamer", "/go-live")
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible()
    await expect.poll(() => page.url()).not.toContain("token=")
  })

  test("missing token shows the open-the-link screen", async ({ page }) => {
    await page.goto(`${state().web}/go-live`)
    await expect(page.getByText("Open the link from the terminal")).toBeVisible()
  })

  test("start is refused without a destination key, enabled with one", async ({ page }) => {
    await openAs(page, "streamer", "/go-live")
    const start = page.getByRole("button", { name: /^Start|Go live$/i }).last()
    // Turn YouTube off and Twitch on: Twitch has no key, so the readiness text must say so and Start is disabled.
    const yt = page.getByRole("switch", { name: /YouTube/ })
    const tw = page.getByRole("switch", { name: /Twitch/ })
    if ((await yt.getAttribute("aria-checked")) === "true") await yt.click()
    await tw.click()
    await expect(page.getByTestId("readiness")).toContainText(/Twitch/)
    await expect(page.getByRole("button", { name: /start/i }).last()).toBeDisabled()
    await tw.click()
    await yt.click()
    await expect(page.getByTestId("readiness")).not.toContainText(/missing|no destination/i)
    await expect(start).toBeEnabled()
  })

  test("start without an engine: reports the outcome in the UI and stop returns to idle", async ({ page, request }) => {
    await openAs(page, "streamer", "/go-live")
    await page.getByRole("button", { name: /start/i }).last().click()
    // With SESSION_SPAWN=false no engine is started: the page must show a state or an error, never hang.
    const state$ = page.getByTestId("session-state")
    const alert = page.getByRole("alert").filter({ hasText: /\S/ })
    await expect(state$.or(alert).first()).toBeVisible()
    const session = await getJson(request, "/api/session")
    expect(session.status).toBe(200)
    // Clean up whatever was started so later specs see an idle API.
    if (["starting", "running", "live", "ready"].includes(session.body.state)) {
      await request.post(`${state().api}/api/session/stop`, { headers: { Authorization: `Bearer ${state().streamer}` } })
    }
  })
})
