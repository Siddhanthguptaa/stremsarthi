import "@fontsource-variable/noto-sans"
import "@fontsource-variable/noto-sans-devanagari"
import "./globals.css"

import type { Metadata, Viewport } from "next"

import { AppHeader } from "@/components/shell/app-header"
import { AuthProvider } from "@/components/shell/auth-provider"
import { LanguageProvider } from "@/components/shell/language-provider"
import { Toaster } from "@/components/ui/toaster"

export const metadata: Metadata = {
  title: { default: "Stream Sarthi", template: "%s | Stream Sarthi" },
  description: "Privacy and safety layer for Indian live streamers. स्ट्रीम सारथी",
  referrer: "no-referrer",
}

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
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
            <main id="main" className="mx-auto w-full max-w-5xl px-4 py-6 pb-safe">
              {children}
            </main>
            <Toaster />
          </AuthProvider>
        </LanguageProvider>
      </body>
    </html>
  )
}
