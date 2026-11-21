import type { Metadata } from "next"

import { LandingPage } from "@/components/landing/landing-page"

export const metadata: Metadata = {
  title: { absolute: "Streamify: go live, leak nothing" },
  description:
    "Streamify holds your stream for about 4 seconds and masks private information in video, audio and chat before viewers see it.",
}

export default function Home() {
  return <LandingPage />
}
