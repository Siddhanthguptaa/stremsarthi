import { expect, test } from "@playwright/test"
import { openAs } from "./helpers"

test("camera permission denied shows the blocked message with a retry, no stuck spinner", async ({ page }) => {
  await openAs(page, "streamer", "/enrol")
  await expect(page.getByRole("heading", { level: 1, name: "Add faces" })).toBeVisible()
  await page.getByLabel("Name or label").fill("SPECIMEN tester")
  await page.getByRole("button", { name: "Start camera" }).click()
  await expect(page.getByText("Camera access is blocked")).toBeVisible()
  await expect(page.getByRole("button", { name: "Try again" })).toBeVisible()
  await expect(page.getByText("Asking for camera access")).toHaveCount(0)
  await expect(page.getByText("Nobody is enrolled yet")).toBeVisible()
})
