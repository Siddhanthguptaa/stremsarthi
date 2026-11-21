/*
 * Adapted from Saximn/privastream (Apache-2.0), src/web/frontend/components/enrollment/facepreview.tsx.
 * See LICENSE.privastream. Changed: it is now one row of the allowlist (label, expiry, remove) and shows a
 * generic avatar only. There is no thumbnail anywhere: the app never keeps or shows a picture of a face.
 */
"use client"

import { Eye, Trash2, User } from "lucide-react"

import type { EnrolledFace } from "@/lib/api"
import { expiryInfo } from "@/lib/enrol"
import { useLanguage } from "@/components/shell/language-provider"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"

interface FaceCardProps {
  face: EnrolledFace
  nowMs: number
  removing?: boolean
  onRemove: (face: EnrolledFace) => void
}

export function FaceCard({ face, nowMs, removing = false, onRemove }: FaceCardProps) {
  const { t } = useLanguage()
  const expiry = expiryInfo(face.expires_at, nowMs)

  return (
    <Card className="flex flex-col gap-3 p-3" data-face-id={face.face_id}>
      <div className="flex items-center gap-3">
        <Avatar className="size-12 shrink-0 rounded-md">
          <AvatarFallback aria-hidden className="rounded-md">
            <User className="size-6" />
          </AvatarFallback>
        </Avatar>
        <p className="min-w-0 flex-1 break-words font-display text-xl font-bold leading-tight">{face.label}</p>
        <Button
          variant="outline"
          size="icon"
          className="size-11 shrink-0"
          disabled={removing}
          onClick={() => onRemove(face)}
          aria-label={`${t("enrol.remove")}: ${face.label}`}
        >
          <Trash2 aria-hidden />
        </Button>
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
        <Badge variant="secondary" className="gap-1">
          <Eye aria-hidden className="size-3.5" />
          {t("enrol.visible")}
        </Badge>
        <span data-testid="expiry">
          {expiry.kind === "never" ? t("enrol.noExpiry") : null}
          {expiry.kind === "expired" ? t("enrol.expired") : null}
          {expiry.kind === "in"
            ? `${t("enrol.expiresIn")} ${expiry.hours} ${t("unit.h")} ${expiry.minutes} ${t("unit.min")}`
            : null}
        </span>
      </div>
    </Card>
  )
}
