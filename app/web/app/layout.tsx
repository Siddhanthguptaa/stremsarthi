import "@fontsource-variable/big-shoulders-display"
import "@fontsource-variable/hanken-grotesk"
import "@fontsource-variable/noto-sans-devanagari"
import "./globals.css"

import type { Metadata, Viewport } from "next"

import { AppHeader } from "@/components/shell/app-header"
import { AuthProvider } from "@/components/shell/auth-provider"
import { LanguageProvider } from "@/components/shell/language-provider"
import { Toaster } from "@/components/ui/toaster"

export const metadata: Metadata = {
  title: { default: "Streamify", template: "%s | Streamify" },
  description: "Privacy and safety layer for Indian live streamers.",
  referrer: "no-referrer",
}

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-dvh">
        <LanguageProvider>
          <a
            href="#main"
            className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded-md focus:bg-primary focus:px-4 focus:py-2 focus:text-primary-foreground"
          >
            Skip to main content
          </a>
          <AuthProvider>
            <AppHeader />
            <div className="md:pl-60">
              <main
                id="main"
                className="mx-auto w-full max-w-6xl px-4 pb-[calc(var(--bottom-nav-h)+1.5rem)] pt-6 md:px-8 md:pb-10 md:pt-8"
              >
                {children}
              </main>
            </div>
            <Toaster />
          </AuthProvider>
        </LanguageProvider>
      </body>
    </html>
  )
}
