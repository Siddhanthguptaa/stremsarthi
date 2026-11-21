"use client"

import * as React from "react"

import { Button } from "@/components/ui/button"
import { useLanguage } from "@/components/shell/language-provider"
import { lt, type LandingKey } from "@/lib/landing-text"
import { cn } from "@/lib/utils"

/**
 * The redaction reel: a mock stream frame (all made-up, nothing real) where six masks snap on one by one.
 * Motion is only a reveal; the ledger beside it says the same thing in text, and with reduced motion
 * the reel simply shows its final, masked state.
 */

const W = 960
const H = 540

type Modality = "video" | "audio" | "chat"
interface Mask {
  id: string
  m: Modality
  x: number
  y: number
  w: number
  h: number
  label: LandingKey
}

const MASKS: Mask[] = [
  { id: "face", m: "video", x: 172, y: 112, w: 72, h: 82, label: "reel.i1" },
  { id: "otp", m: "video", x: 326, y: 104, w: 118, h: 66, label: "reel.i2" },
  { id: "qr", m: "video", x: 494, y: 114, w: 162, h: 182, label: "reel.i3" },
  { id: "id", m: "video", x: 326, y: 386, w: 258, h: 98, label: "reel.i4" },
  { id: "chat", m: "chat", x: 692, y: 306, w: 236, h: 70, label: "reel.i5" },
  { id: "voice", m: "audio", x: 126, y: 334, w: 112, h: 76, label: "reel.i6" },
]

const START_MS = 800
const STEP_MS = 650
const pct = (v: number, total: number) => `${(v / total) * 100}%`

const CHIP: Record<Modality, string> = {
  video: "bg-video text-primary-foreground",
  audio: "bg-audio text-success-foreground",
  chat: "bg-chat text-primary-foreground",
}
const OUTLINE: Record<Modality, string> = {
  video: "outline-video",
  audio: "outline-audio",
  chat: "outline-chat",
}

/** A tiny deterministic QR look-alike. It encodes nothing. */
function qrCells(): { x: number; y: number }[] {
  const n = 11
  const out: { x: number; y: number }[] = []
  let seed = 7
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) {
      const inFinder = (x < 4 && y < 4) || (x > n - 5 && y < 4) || (x < 4 && y > n - 5)
      if (inFinder) continue
      seed = (seed * 31 + 17) % 97
      if (seed % 2 === 0) out.push({ x, y })
    }
  }
  return out
}
const QR = qrCells()

function Scene() {
  const finder = (x: number, y: number, s: number) => (
    <g>
      <rect x={x} y={y} width={s} height={s} fill="hsl(var(--background))" />
      <rect x={x + s * 0.16} y={y + s * 0.16} width={s * 0.68} height={s * 0.68} fill="hsl(var(--foreground))" />
      <rect x={x + s * 0.32} y={y + s * 0.32} width={s * 0.36} height={s * 0.36} fill="hsl(var(--background))" />
    </g>
  )
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="absolute inset-0 size-full" aria-hidden focusable="false">
      <rect width={W} height={H} fill="hsl(var(--background))" />
      <rect y={410} width={W} height={130} fill="hsl(var(--card))" />
      <rect y={408} width={W} height={4} fill="hsl(var(--border))" />

      {/* Webcam bubble: the streamer (on the allowlist, ringed in safe teal) and a bystander behind */}
      <circle cx={150} cy={160} r={106} fill="hsl(var(--desk-raised))" />
      <ellipse cx={208} cy={152} rx={26} ry={32} fill="hsl(var(--muted-foreground))" opacity={0.7} />
      <path d="M170 262 Q208 200 246 262 Z" fill="hsl(var(--muted-foreground))" opacity={0.5} />
      <ellipse cx={122} cy={168} rx={38} ry={46} fill="hsl(var(--foreground))" opacity={0.92} />
      <path d="M50 262 Q122 190 194 262 Q150 266 122 266 Z" fill="hsl(var(--foreground))" opacity={0.85} />
      <circle cx={108} cy={162} r={4} fill="hsl(var(--background))" />
      <circle cx={136} cy={162} r={4} fill="hsl(var(--background))" />
      <path d="M110 184 Q122 194 134 184" stroke="hsl(var(--background))" strokeWidth={3} fill="none" strokeLinecap="round" />
      <circle cx={150} cy={160} r={106} fill="none" stroke="hsl(var(--success))" strokeWidth={5} />

      {/* Phone with an OTP banner */}
      <rect x={316} y={66} width={138} height={262} rx={20} fill="hsl(var(--card))" stroke="hsl(var(--border))" strokeWidth={4} />
      <rect x={326} y={96} width={118} height={212} rx={8} fill="hsl(var(--desk-raised))" />
      <rect x={328} y={106} width={114} height={62} rx={10} fill="hsl(var(--foreground))" />
      <text x={340} y={130} fontSize={13} fontWeight={700} fill="hsl(var(--background))" fontFamily="var(--font-sans)">
        Bank SMS
      </text>
      <text x={340} y={154} fontSize={20} fontWeight={800} fill="hsl(var(--background))" fontFamily="var(--font-display)">
        OTP XXXXXX
      </text>
      <rect x={340} y={196} width={90} height={8} rx={4} fill="hsl(var(--border))" />
      <rect x={340} y={216} width={64} height={8} rx={4} fill="hsl(var(--border))" />
      <rect x={340} y={236} width={78} height={8} rx={4} fill="hsl(var(--border))" />

      {/* UPI QR sticker */}
      <g transform="rotate(-3 575 205)">
        <rect x={500} y={118} width={150} height={174} rx={10} fill="hsl(var(--foreground))" />
        <rect x={500} y={118} width={150} height={28} rx={10} fill="hsl(var(--primary))" />
        <text x={575} y={138} fontSize={17} fontWeight={800} textAnchor="middle" fill="hsl(var(--background))" fontFamily="var(--font-display)">
          UPI SPECIMEN
        </text>
        <g transform="translate(514 156)">
          {QR.map((c) => (
            <rect key={`${c.x}-${c.y}`} x={c.x * 11} y={c.y * 11} width={11} height={11} fill="hsl(var(--background))" />
          ))}
          {finder(0, 0, 44)}
          {finder(77, 0, 44)}
          {finder(0, 77, 44)}
        </g>
      </g>

      {/* ID card, marked SPECIMEN */}
      <rect x={330} y={352} width={250} height={128} rx={10} fill="hsl(var(--foreground))" />
      <rect x={330} y={352} width={250} height={32} rx={10} fill="hsl(var(--primary))" />
      <text x={455} y={375} fontSize={22} fontWeight={800} textAnchor="middle" fill="hsl(var(--background))" fontFamily="var(--font-display)">
        SPECIMEN ID: NOT REAL
      </text>
      <rect x={346} y={398} width={58} height={70} rx={4} fill="hsl(var(--border))" />
      <text x={420} y={426} fontSize={20} fontWeight={700} fill="hsl(var(--background))" fontFamily="var(--font-display)">
        XXXX XXXX XXXX
      </text>
      <rect x={420} y={440} width={130} height={8} rx={4} fill="hsl(var(--border))" />
      <rect x={420} y={456} width={96} height={8} rx={4} fill="hsl(var(--border))" />

      {/* Mic level: the bars are speech, the masked stretch is a spoken number */}
      <rect x={40} y={322} width={260} height={92} rx={10} fill="hsl(var(--card))" stroke="hsl(var(--border))" strokeWidth={3} />
      {Array.from({ length: 22 }, (_, i) => {
        const h = 14 + ((i * 37) % 46)
        return <rect key={i} x={54 + i * 11} y={368 - h / 2} width={6} height={h} rx={3} fill="hsl(var(--audio))" opacity={0.85} />
      })}

      {/* Chat column */}
      <rect x={684} y={64} width={252} height={440} rx={12} fill="hsl(var(--card))" stroke="hsl(var(--border))" strokeWidth={3} />
      {[
        { n: "rani_plays", t: "gg bhai, great round", c: "--audio" },
        { n: "veer07", t: "kal ka stream kab hai", c: "--chat" },
        { n: "mod_asha", t: "welcome everyone", c: "--primary" },
        { n: "troll_x", t: "uska ghar: flat 4B, XXXX", c: "--tally" },
        { n: "kunal.k", t: "first time here, nice", c: "--chat" },
      ].map((r, i) => (
        <g key={r.n} transform={`translate(700 ${86 + i * 78})`}>
          <text fontSize={15} fontWeight={700} fill={`hsl(var(${r.c}))`} fontFamily="var(--font-sans)">
            {r.n}
          </text>
          <text y={26} fontSize={15} fill="hsl(var(--foreground))" fontFamily="var(--font-sans)">
            {r.t}
          </text>
        </g>
      ))}
    </svg>
  )
}

function Timecode({ run }: { run: boolean }) {
  const [sec, setSec] = React.useState(4)
  React.useEffect(() => {
    if (!run) return
    const first = window.setTimeout(() => setSec(0), 0)
    const id = window.setInterval(() => setSec((s) => s + 1), 1000)
    return () => {
      window.clearTimeout(first)
      window.clearInterval(id)
    }
  }, [run])
  const pad = (n: number) => String(n).padStart(2, "0")
  return (
    <span className="timecode text-sm leading-none tabular-nums text-foreground sm:text-lg" data-testid="reel-timecode">
      {`00:${pad(Math.floor(sec / 60))}:${pad(sec % 60)}`}
    </span>
  )
}

export function Reel({ className }: { className?: string }) {
  const { lang } = useLanguage()
  const [done, setDone] = React.useState(MASKS.length) // server render and reduced motion: final state
  const [run, setRun] = React.useState(false)
  const [round, setRound] = React.useState(0)
  const timers = React.useRef<number[]>([])

  const clear = () => {
    timers.current.forEach((id) => window.clearTimeout(id))
    timers.current = []
  }

  React.useLayoutEffect(() => {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return clear
    // Everything starts from a timer so the effect never sets state synchronously.
    timers.current.push(
      window.setTimeout(() => {
        setDone(0)
        setRun(true)
      }, 0),
    )
    MASKS.forEach((_, i) => {
      timers.current.push(window.setTimeout(() => setDone(i + 1), START_MS + i * STEP_MS))
    })
    return clear
  }, [round])

  const replay = () => {
    clear()
    setDone(0)
    setRound((r) => r + 1)
  }

  return (
    <section className={cn("min-w-0", className)} aria-labelledby="reel-ledger-title">
      <div className="module p-2 sm:p-3">
        <div className="relative aspect-video w-full overflow-hidden rounded-md border" data-testid="reel-frame">
          <div
            role="img"
            aria-label={lt(lang, "reel.label")}
            aria-describedby="reel-desc"
            className="absolute inset-0"
          >
            <Scene />
            {MASKS.map((mk, i) => {
              const masked = i < done
              return (
                <div
                  key={mk.id}
                  data-masked={masked}
                  className={cn(
                    "absolute rounded-[3px]",
                    masked ? "outline-0" : cn("outline outline-2 outline-dashed outline-offset-1", OUTLINE[mk.m]),
                  )}
                  style={{
                    left: pct(mk.x, W),
                    top: pct(mk.y, H),
                    width: pct(mk.w, W),
                    height: pct(mk.h, H),
                  }}
                >
                  <div
                    className={cn(
                      "mosaic absolute inset-0 rounded-[3px] transition-[opacity,transform] duration-150 ease-out",
                      `mosaic-${mk.m}`,
                      masked ? "scale-100 opacity-100" : "scale-110 opacity-0",
                    )}
                  />
                  <span
                    className={cn(
                      "absolute -left-1 -top-2.5 flex size-4 items-center justify-center rounded-sm text-[10px] font-extrabold leading-none sm:size-5 sm:text-xs",
                      CHIP[mk.m],
                    )}
                  >
                    {i + 1}
                  </span>
                </div>
              )
            })}
            <div className="absolute inset-x-0 top-0 flex h-[11%] items-center justify-between border-b bg-background px-2.5 sm:px-4">
              <span className="inline-flex items-center gap-1.5 rounded-sm bg-destructive px-1.5 py-0.5 text-[10px] font-extrabold leading-none tracking-wider text-destructive-foreground sm:gap-2 sm:px-2.5 sm:py-1 sm:text-sm">
                <span className="size-1.5 rounded-full bg-destructive-foreground sm:size-2" />
                {lt(lang, "reel.onair")}
              </span>
              <Timecode run={run} />
            </div>
          </div>
        </div>
        <p id="reel-desc" className="sr-only">
          {lt(lang, "reel.desc")}
        </p>

        <div className="mt-3 flex items-center justify-between gap-3 px-1">
          <h2 id="reel-ledger-title" className="text-xl font-bold">
            {lt(lang, "reel.ledger")}
          </h2>
          <Button type="button" variant="ghost" size="sm" className="min-h-11" onClick={replay}>
            {lt(lang, "reel.replay")}
          </Button>
        </div>
        <ol className="mt-1 grid gap-1.5 sm:grid-cols-2">
          {MASKS.map((mk, i) => {
            const masked = i < done
            return (
              <li key={mk.id} className="module-row flex min-h-11 items-center gap-2.5 px-3 py-1.5">
                <span
                  aria-hidden
                  className={cn("flex size-5 shrink-0 items-center justify-center rounded-sm text-xs font-extrabold", CHIP[mk.m])}
                >
                  {i + 1}
                </span>
                <span className="min-w-0 flex-1 text-sm leading-tight">{lt(lang, mk.label)}</span>
                <span
                  className={cn(
                    "flex shrink-0 items-center gap-1.5 text-xs font-semibold",
                    masked ? "text-success" : "text-muted-foreground",
                  )}
                >
                  <span className={cn("led", masked ? "text-success" : "text-muted-foreground")} />
                  {masked ? lt(lang, "reel.done") : lt(lang, "reel.waiting")}
                </span>
              </li>
            )
          })}
        </ol>
        <p className="mt-2 px-1 text-xs text-muted-foreground">{lt(lang, "reel.ledger.note")}</p>
      </div>
    </section>
  )
}
