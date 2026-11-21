// @vitest-environment jsdom
import { screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { LandingPage } from "@/components/landing/landing-page"
import { landingEn, landingHi } from "@/lib/landing-text"
import { renderIn } from "./dom-helpers"

describe("landing page", () => {
  it("has exactly one h1", () => {
    renderIn(<LandingPage />)
    const h1 = screen.getAllByRole("heading", { level: 1 })
    expect(h1).toHaveLength(1)
    expect(h1[0].textContent).toContain("Go live.")
    expect(h1[0].textContent).toContain("Leak nothing.")
  })

  it("links the two calls to action", () => {
    renderIn(<LandingPage />)
    const setup = screen.getAllByRole("link", { name: "Set up your stream" })
    const demo = screen.getAllByRole("link", { name: "Watch the demo" })
    expect(setup[0].getAttribute("href")).toBe("/go-live")
    expect(demo[0].getAttribute("href")).toBe("/demo")
  })

  it("gives the reel an accessible name and description", () => {
    renderIn(<LandingPage />)
    const reel = screen.getByRole("img", { name: /redaction reel/i })
    const id = reel.getAttribute("aria-describedby")
    expect(id).toBeTruthy()
    const desc = document.getElementById(id as string)
    expect(desc?.textContent?.length ?? 0).toBeGreaterThan(80)
    expect(desc?.textContent).toContain("SPECIMEN")
  })

  it("lists every masked item in the ledger", () => {
    renderIn(<LandingPage />)
    expect(screen.getByText("UPI QR code")).toBeTruthy()
  })

  it("has Hindi for every English string", () => {
    expect(Object.keys(landingHi).sort()).toEqual(Object.keys(landingEn).sort())
  })
})
