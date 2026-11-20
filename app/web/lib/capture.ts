/**
 * Browser-side camera helpers for enrolment. Everything stays in memory: one off-screen canvas that is
 * cleared when the burst ends, JPEG blobs handed to the caller, and a MediaStream whose tracks the caller
 * stops. Tests replace these through `CaptureDeps`.
 */

import { CAPTURE_WIDTH, type FaceInfo, type FrameStats, frameStats, JPEG_QUALITY } from "@/lib/enrol"

export interface FrameGrabber {
  /** Encode the current video frame (unmirrored) as a JPEG, at most `CAPTURE_WIDTH` pixels wide. */
  grab(video: HTMLVideoElement): Promise<Blob>
  /** Clear and release the canvas. */
  dispose(): void
}

export interface CaptureDeps {
  getUserMedia: (constraints: MediaStreamConstraints) => Promise<MediaStream>
  createGrabber: () => FrameGrabber
  /** Thumbnail statistics and an optional face count, for live guidance. */
  sample: (video: HTMLVideoElement) => Promise<{ stats: FrameStats; faces: FaceInfo | null }>
  sleep: (ms: number) => Promise<void>
}

export function createCanvasGrabber(): FrameGrabber {
  let canvas: HTMLCanvasElement | null = document.createElement("canvas")
  return {
    grab(video) {
      return new Promise<Blob>((resolve, reject) => {
        const c = canvas
        const w = video.videoWidth
        const h = video.videoHeight
        const ctx = c?.getContext("2d")
        if (!c || !ctx || !w || !h) {
          reject(new Error("no frame"))
          return
        }
        const scale = Math.min(1, CAPTURE_WIDTH / w)
        c.width = Math.round(w * scale)
        c.height = Math.round(h * scale)
        ctx.drawImage(video, 0, 0, c.width, c.height)
        c.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("no frame"))), "image/jpeg", JPEG_QUALITY)
      })
    },
    dispose() {
      if (canvas) {
        const ctx = canvas.getContext("2d")
        ctx?.clearRect(0, 0, canvas.width, canvas.height)
        canvas.width = 0
        canvas.height = 0
      }
      canvas = null
    },
  }
}

interface BrowserFaceDetector {
  detect(source: CanvasImageSource): Promise<{ boundingBox: { width: number } }[]>
}
type FaceDetectorCtor = new (options?: { fastMode?: boolean; maxDetectedFaces?: number }) => BrowserFaceDetector

const THUMB_W = 160
const THUMB_H = 120

/** Statistics from a small thumbnail plus, where the browser has `FaceDetector`, a face count. */
export async function sampleVideo(video: HTMLVideoElement): Promise<{ stats: FrameStats; faces: FaceInfo | null }> {
  const canvas = document.createElement("canvas")
  canvas.width = THUMB_W
  canvas.height = THUMB_H
  try {
    const ctx = canvas.getContext("2d", { willReadFrequently: true })
    if (!ctx || !video.videoWidth) return { stats: { luma: 128, sharpness: 100 }, faces: null }
    ctx.drawImage(video, 0, 0, THUMB_W, THUMB_H)
    const stats = frameStats(ctx.getImageData(0, 0, THUMB_W, THUMB_H).data, THUMB_W, THUMB_H)
    let faces: FaceInfo | null = null
    const Detector = (globalThis as { FaceDetector?: FaceDetectorCtor }).FaceDetector
    if (Detector) {
      try {
        const found = await new Detector({ fastMode: true, maxDetectedFaces: 3 }).detect(canvas)
        faces = {
          count: found.length,
          largestWidthRatio: found.reduce((m, f) => Math.max(m, f.boundingBox.width), 0) / THUMB_W,
        }
      } catch {
        faces = null // detector unavailable or failed: brightness and sharpness guidance only
      }
    }
    return { stats, faces }
  } finally {
    canvas.width = 0
    canvas.height = 0
  }
}

export const defaultCaptureDeps = (): CaptureDeps => ({
  getUserMedia: (constraints) => navigator.mediaDevices.getUserMedia(constraints),
  createGrabber: createCanvasGrabber,
  sample: sampleVideo,
  sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
})

export function stopStream(stream: MediaStream | null): void {
  stream?.getTracks().forEach((track) => track.stop())
}
