# Streamify design: "Broadcast desk"

Streamify is the streamer's safety co-pilot. The UI is a piece of broadcast hardware:
tally lights, condensed scoreboard type, keys you press, and a **delay tape** that shows each
redaction travelling down the delay window toward "on air". Dark only.

Audience: a streamer glancing at a phone or a second monitor mid-stream (often stressed), and
hackathon judges seeing it for the first time. Primary job: confidence that nothing leaks, and
one press to hold.

## Tokens (all in `app/globals.css` and `tailwind.config.js`)

| Token | Value | Use |
|---|---|---|
| `bg-background` | studio ink #121031 | page |
| `bg-card` / `module` | desk #1B1844 | every panel |
| `bg-desk-raised` | #241F57 | hover, active nav, inputs that sit on a module |
| `border` | desk line #34306B | hairlines |
| `text-foreground` | chalk #F3F0FF | text |
| `text-muted-foreground` | #B4AEDD | secondary text |
| `primary` | marigold #FFB627 | brand, main action keys, focus ring |
| `destructive` / `tally` | tally red #EB1443 / #FF3D5E | **on air and HOLD only** (plus real errors) |
| `success` | safe teal #26E0B0 | protected / healthy |
| `warning` | signal orange #FF8A3D | warnings |
| `video` / `audio` / `chat` | marigold / teal / lilac #9B8CFF | colour of an event by where it came from |

Colour always encodes meaning: modality (video/audio/chat), or state (on air, safe, warning). No decorative
gradients, no glow washes, no glass blur.

## Type

- `font-display` = Big Shoulders Display (condensed). h1/h2/h3 get it automatically. Use it for page titles,
  big numbers, the tally label, timecodes (`.timecode` = display + tabular nums).
- Body = Hanken Grotesk. Hindi falls back to Noto Sans Devanagari in both.
- Scale: page title `text-4xl md:text-5xl font-extrabold leading-[1.05]`; module title `text-xl font-bold`;
  body `text-sm`/`text-base`. Sentence case everywhere. **No ALL-CAPS tracked eyebrow labels**, no
  "A · B · C" meta strings, no "→" on buttons.

## Components (CSS classes in `globals.css`)

- `.module`: the desk panel (rounded-lg, hairline border, bg-card, faint top highlight). The `Card` component
  is a module. **Never nest modules.** Rows inside a module use `.module-row` (darker inset, rounded-md).
- `.keycap`: physical key depth (inset bottom edge, moves 1px on press). The solid Button variants already
  have it. Use it for big custom keys too.
- `.led` + `text-*`: status LED dot (`<span className="led text-success" />`). Pair with a text label,
  never colour alone.
- `.mosaic` (+ `.mosaic-video|audio|chat`): the redaction mosaic, the brand's mask. Use it wherever we show
  "something was hidden here" (delay tape blocks, masked values, empty placeholders for redacted data).
- `.hazard`: yellow/ink tape. **HOLD control only.**
- `MosaicMark` / `Wordmark` in `components/shell/wordmark.tsx`.

## Page structure

- Shell (done): left rail on md+ (`md:pl-60` content offset), top bar + fixed bottom tab bar on phones.
  `--bottom-nav-h` is the tab bar height on phones and `0px` on md+. Anything fixed to the bottom (the HOLD
  bar) must use `bottom: var(--bottom-nav-h)` and `md:left-60`.
- Page header pattern: a `<header>` with the h1 (display, big) and one line of muted description. Status
  for the page (LEDs, chips) can sit on the right of the header on md+.
- Layout: real grids, not a single column of stacked cards. On md+ use 2 or 3 column grids
  (`grid gap-4 lg:grid-cols-[1.4fr_1fr]` etc). Single column on phones.

## Motion

- One orchestrated moment per screen at most. Motion otherwise only answers a user action or new data
  (a new event entering the delay tape, a meter changing level).
- Everything must respect `prefers-reduced-motion` (globals cuts durations; don't rely on motion to
  convey information).

## Copy

Plain verbs, sentence case, the streamer's words ("Hold", "Faces that stay visible"), not system words.
Errors say what happened and what to do. Keep every existing i18n string; new strings need English and
Hindi.

## Accessibility and privacy floors (do not regress)

- Keep every `role`, `aria-*`, `data-testid`, and visible string that tests assert on.
- 44px minimum touch targets, visible focus ring, AA contrast.
- Never show images or raw PII. The report shows category, time and confidence only.
