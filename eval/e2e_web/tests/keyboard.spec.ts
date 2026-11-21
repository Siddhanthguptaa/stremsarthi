import { expect, test } from "@playwright/test"
import { apiHold, openAs } from "./helpers"

test.afterAll(async ({ request }) => {
  await apiHold(request, false)
})

test("keyboard only: Tab reaches HOLD within 6 presses on /live, Space and Enter hold, resume by keyboard", async ({ page, request }) => {
  await apiHold(request, false)
  await openAs(page, "streamer", "/live")
  const btn = page.locator("button[data-phase]")
  await expect(btn).toHaveAttribute("data-phase", "released", { timeout: 10_000 })
  await page.locator("body").click({ position: { x: 2, y: 2 } })
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
  let presses = 0
  for (; presses < 12; presses++) {
    await page.keyboard.press("Tab")
    const on = await page.evaluate(() => document.activeElement?.hasAttribute("data-phase") ?? false)
    if (on) break
  }
  const used = presses + 1
  test.info().annotations.push({ type: "tab-presses-to-hold", description: String(used) })
  expect(used, `Tab presses to reach HOLD: ${used} (spec: <= 6)`).toBeLessThanOrEqual(6)
  await page.keyboard.press("Space")
  await expect(btn).toHaveAttribute("data-phase", "holding")
  await page.keyboard.press("Enter")
  await expect(page.getByRole("dialog")).toBeVisible()
  await page.keyboard.press("Escape")
  await expect(page.getByRole("dialog")).toHaveCount(0)
  await expect(btn).toBeFocused() // focus comes back to the button after the dialog
  await page.keyboard.press("Enter")
  await expect(page.getByRole("dialog")).toBeVisible()
  await page.getByRole("button", { name: "Resume streaming" }).focus()
  await page.keyboard.press("Enter")
  await expect(btn).toHaveAttribute("data-phase", "released")
})

test("focus is visible on the hold button", async ({ page }) => {
  await openAs(page, "streamer", "/live")
  const btn = page.locator("button[data-phase]")
  await expect(btn).toBeVisible({ timeout: 10_000 })
  await btn.focus()
  const outline = await btn.evaluate((el) => {
    const s = getComputedStyle(el)
    return { ring: s.boxShadow, outline: s.outlineStyle + s.outlineWidth }
  })
  expect(outline.ring + outline.outline).not.toMatch(/^none$/)
})
