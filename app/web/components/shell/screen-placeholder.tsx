"use client"

import { ShieldCheck } from "lucide-react"

import { type I18nKey } from "@/lib/i18n"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { useLanguage } from "@/components/shell/language-provider"

interface ScreenPlaceholderProps {
  titleKey: I18nKey
  descKey: I18nKey
  /** Extra detail such as a route parameter (a session id is an opaque code, not PII). */
  detail?: string
}

/** Placeholder body for screens that later tasks fill in. One h1 per page. */
export function ScreenPlaceholder({ titleKey, descKey, detail }: ScreenPlaceholderProps) {
  const { t } = useLanguage()
  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-bold tracking-tight">{t(titleKey)}</h1>
      <p className="text-muted-foreground">{t(descKey)}</p>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <ShieldCheck aria-hidden className="size-5 text-success" />
            {t("placeholder.soon")}
          </CardTitle>
          <CardDescription>{t("placeholder.privacy")}</CardDescription>
        </CardHeader>
        {detail ? (
          <CardContent>
            <code className="text-sm">{detail}</code>
          </CardContent>
        ) : null}
      </Card>
    </section>
  )
}
