import { expect, test } from "@playwright/test"
import { axeScan, openAs, scenario } from "./helpers"
import { ROUTES } from "./routes"

test.beforeAll(async ({ request }) => {
  await scenario(request, "seed_report", { session_id: "e2e-session-0001", n: 40 })
  await scenario(request, "chat_hidden", { id: "axe1", text: "SPECIMEN flagged message" })
})

for (const vp of [{ width: 1280, height: 720 }, { width: 360, height: 640 }]) {
  for (const r of ROUTES) {
    test(`axe: ${r.name} at ${vp.width}px has no serious or critical violations`, async ({ page }) => {
      await page.setViewportSize(vp)
      await openAs(page, r.role, r.path)
      await expect(page.getByRole("heading").first()).toBeVisible({ timeout: 10_000 })
      await page.waitForTimeout(1000)
      const bad = await axeScan(page, r.name)
      expect(bad, JSON.stringify(bad, null, 1)).toEqual([])
    })
  }
}

test("axe: dialogs (resume confirmation) have no serious or critical violations", async ({ page, request }) => {
  await openAs(page, "streamer", "/live")
  const btn = page.locator("button[data-phase]")
  await expect(btn).toBeVisible({ timeout: 10_000 })
  await btn.click()
  await expect(btn).toHaveAttribute("data-phase", "holding")
  await btn.click()
  await expect(page.getByRole("dialog")).toBeVisible()
  const bad = await axeScan(page, "live-resume-dialog")
  await page.getByRole("button", { name: "Resume streaming" }).click()
  expect(bad, JSON.stringify(bad, null, 1)).toEqual([])
  void request
})
