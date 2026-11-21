import { expect, test } from "@playwright/test"
import { noHorizontalScroll, openAs, scenario } from "./helpers"
import { ROUTES } from "./routes"

for (const width of [360, 320]) {
  test.describe(`no horizontal scroll at ${width}px`, () => {
    test.use({ viewport: { width, height: 640 } })
    test.beforeAll(async ({ request }) => {
      await scenario(request, "seed_report", { session_id: "e2e-session-0001", n: 40 })
      for (const id of ["w1", "w2"]) await scenario(request, "chat_hidden", { id: `mob${id}`, text: "SPECIMEN long message ".repeat(12), author: "SPECIMEN author with a very long display name" })
    })
    for (const r of ROUTES) {
      test(`${r.name}`, async ({ page }) => {
        await openAs(page, r.role, r.path)
        await expect(page.getByRole("heading").first()).toBeVisible({ timeout: 10_000 })
        await page.waitForTimeout(800)
        const m = await noHorizontalScroll(page)
        expect(m.scroll, `scrollWidth ${m.scroll} > clientWidth ${m.client}`).toBeLessThanOrEqual(m.client)
        await page.screenshot({ path: `/tmp/ss-e2e-shots/${r.name}-${width}.png`, fullPage: true })
      })
    }
  })
}

test.describe("phone hold button", () => {
  test.use({ viewport: { width: 360, height: 640 } })
  test("hold stays visible and at least 72 px tall on /live", async ({ page }) => {
    await openAs(page, "streamer", "/live")
    const btn = page.locator("button[data-phase]")
    await expect(btn).toBeVisible({ timeout: 10_000 })
    const box = (await btn.boundingBox())!
    expect(box.height).toBeGreaterThanOrEqual(72)
    expect(box.y + box.height).toBeLessThanOrEqual(640)
  })
})
