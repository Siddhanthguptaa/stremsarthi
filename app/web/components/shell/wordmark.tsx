import { cn } from "@/lib/utils"

/**
 * The Streamify mark: a 3x3 redaction mosaic (some pixels masked, one tally-red "on air" pixel),
 * then the wordmark in the condensed display face.
 */
const PIXELS: { x: number; y: number; c: string; o?: number }[] = [
  { x: 0, y: 0, c: "hsl(var(--video))" },
  { x: 1, y: 0, c: "hsl(var(--video))", o: 0.55 },
  { x: 2, y: 0, c: "hsl(var(--tally))" },
  { x: 0, y: 1, c: "hsl(var(--video))", o: 0.8 },
  { x: 1, y: 1, c: "hsl(var(--video))" },
  { x: 2, y: 1, c: "hsl(var(--chat))", o: 0.85 },
  { x: 0, y: 2, c: "hsl(var(--audio))" },
  { x: 1, y: 2, c: "hsl(var(--video))", o: 0.45 },
  { x: 2, y: 2, c: "hsl(var(--video))", o: 0.8 },
]

export function MosaicMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 30 30" aria-hidden className={cn("size-8 shrink-0", className)}>
      {PIXELS.map((p) => (
        <rect key={`${p.x}${p.y}`} x={p.x * 10 + 1} y={p.y * 10 + 1} width={8} height={8} rx={1.5} fill={p.c} opacity={p.o ?? 1} />
      ))}
    </svg>
  )
}

export function Wordmark({ className, label = "Streamify" }: { className?: string; label?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <MosaicMark />
      <span className="font-display text-[1.75rem] font-extrabold leading-none tracking-wide">{label}</span>
    </span>
  )
}
