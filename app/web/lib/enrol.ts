/**
 * Face enrolment helpers: the capture plan, live guidance and expiry text.
 *
 * Privacy (docs/specs/app-web.md 5.4): camera frames are drawn to an off-screen canvas, encoded to JPEG,
 * sent once and dropped. The canvas is cleared and the MediaStream tracks are stopped as soon as the burst
 * ends. Nothing is written to localStorage, sessionStorage, IndexedDB or the console. The server returns
 * a label and times, never an image or an embedding.
 */

import { ApiRequestError } from "@/lib/api"
import type { I18nKey } from "@/lib/i18n"

export const CAPTURE_FRAMES = 8
export const CAPTURE_DURATION_MS = 3000
export const CAPTURE_WIDTH = 640
export const JPEG_QUALITY = 0.85
export const GUIDANCE_INTERVAL_MS = 500
export const LABEL_MAX = 40

/** When each frame is taken, in ms after the burst starts: evenly spread, the first one immediately. */
export function captureOffsets(frames: number = CAPTURE_FRAMES, durationMs: number = CAPTURE_DURATION_MS): number[] {
  if (frames < 1) return []
  if (frames === 1) return [0]
  return Array.from({ length: frames }, (_, i) => Math.round((i * durationMs) / (frames - 1)))
}

export interface FrameStats {
  /** Mean brightness 0..255. */
  luma: number
  /** Mean absolute horizontal and vertical gradient: low values mean a blurred or flat picture. */
  sharpness: number
}

/** Brightness and sharpness of an RGBA pixel buffer (a small thumbnail is enough). */
export function frameStats(rgba: Uint8ClampedArray, width: number, height: number): FrameStats {
  const n = width * height
  if (n === 0 || rgba.length < n * 4) return { luma: 0, sharpness: 0 }
  const luma = new Float32Array(n)
  let sum = 0
  for (let i = 0; i < n; i++) {
    const v = 0.299 * rgba[i * 4] + 0.587 * rgba[i * 4 + 1] + 0.114 * rgba[i * 4 + 2]
    luma[i] = v
    sum += v
  }
  let grad = 0
  let count = 0
  for (let y = 0; y < height - 1; y++) {
    for (let x = 0; x < width - 1; x++) {
      const i = y * width + x
      grad += Math.abs(luma[i] - luma[i + 1]) + Math.abs(luma[i] - luma[i + width])
      count += 2
    }
  }
  return { luma: sum / n, sharpness: count ? grad / count : 0 }
}

/** Result of an optional browser face detector: how many faces and the widest one as a share of the frame. */
export interface FaceInfo {
  count: number
  largestWidthRatio: number
}

export type GuidanceKey =
  | "ok"
  | "tooDark"
  | "tooBright"
  | "blurry"
  | "noFace"
  | "multiple"
  | "tooSmall"

export const MIN_LUMA = 55
export const MAX_LUMA = 215
export const MIN_SHARPNESS = 3
export const MIN_FACE_WIDTH_RATIO = 0.2

/** The most important thing to fix first. `faces` is null when the browser has no face detector. */
export function guidanceFor(stats: FrameStats, faces: FaceInfo | null): GuidanceKey {
  if (stats.luma < MIN_LUMA) return "tooDark"
  if (stats.luma > MAX_LUMA) return "tooBright"
  if (stats.sharpness < MIN_SHARPNESS) return "blurry"
  if (faces) {
    if (faces.count === 0) return "noFace"
    if (faces.count > 1) return "multiple"
    if (faces.largestWidthRatio < MIN_FACE_WIDTH_RATIO) return "tooSmall"
  }
  return "ok"
}

export type ExpiryInfo = { kind: "never" } | { kind: "expired" } | { kind: "in"; hours: number; minutes: number }

/** When an enrolled face leaves the allowlist. `null` means it stays until it is deleted (persisted). */
export function expiryInfo(expiresAt: string | null, nowMs: number): ExpiryInfo {
  if (expiresAt === null) return { kind: "never" }
  const at = Date.parse(expiresAt)
  if (Number.isNaN(at)) return { kind: "never" }
  const left = at - nowMs
  if (left <= 0) return { kind: "expired" }
  const totalMinutes = Math.ceil(left / 60_000)
  return { kind: "in", hours: Math.floor(totalMinutes / 60), minutes: totalMinutes % 60 }
}

export type CameraFailure = "denied" | "unavailable"

/** Map a getUserMedia rejection to a user-facing state. */
export function cameraFailure(error: unknown): CameraFailure {
  const name = typeof error === "object" && error !== null && "name" in error ? String((error as { name: unknown }).name) : ""
  return name === "NotAllowedError" || name === "SecurityError" || name === "PermissionDeniedError" ? "denied" : "unavailable"
}

const ENROL_ERRORS: Partial<Record<string, I18nKey>> = {
  too_few_faces: "enrol.err.too_few_faces",
  allowlist_full: "enrol.err.allowlist_full",
  enroller_unavailable: "enrol.err.enroller_unavailable",
  rate_limited: "enrol.err.rate_limited",
  busy: "enrol.err.busy",
}

/** A message for a failed enrolment. Only the error code is used: never the server's detail text. */
export function enrolErrorKey(error: unknown): I18nKey {
  if (error instanceof ApiRequestError) {
    if (error.isNetwork) return "enrol.err.network"
    return ENROL_ERRORS[error.code] ?? "enrol.err.generic"
  }
  return "enrol.err.generic"
}

/** Trim and collapse the label the same way the API does (1 to 40 characters). */
export function cleanLabel(raw: string): string {
  return raw.replace(/\s+/g, " ").trim().slice(0, LABEL_MAX)
}
