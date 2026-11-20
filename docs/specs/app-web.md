# Spec: app/web (Next.js streamer app, moderator console, report)

## 1. Purpose and owner
The browser/phone UI: go-live setup, face enrolment, category toggles, hold button, live risk meter, alerts, chat overlay, captions toggle, moderator console, post-stream safety report. Mobile-first: the streamer runs it on their phone while streaming. English UI with Hindi labels.
Owner: **product-engineer** (two instances across waves: T6.2 and T6.3 own disjoint route groups, see section 2).

## 2. Directories owned and file layout
`app/web/` and its tests. Ownership is split by route group so T6.2 and T6.3 (same wave) never edit the same file.
```
app/web/
  package.json  tsconfig.json  next.config.js  tailwind.config.js  postcss.config.js  components.json  eslint.config.mjs   # T0.7 scaffold, then lead-approved changes only
  app/
    layout.tsx  globals.css  page.tsx                  # T6.2 owns (shared shell, providers, language toggle)
    (streamer)/go-live/page.tsx                        # T6.2
    (streamer)/enrol/page.tsx                          # T6.2
    (streamer)/controls/page.tsx                       # T6.2
    (streamer)/live/page.tsx                           # T6.2
    (mod)/mod/page.tsx  (mod)/mod/layout.tsx           # T6.3 (first built in T4.6)
    (report)/report/[sessionId]/page.tsx               # T6.3
  components/
    ui/*                                               # T6.2 (shadcn copies, see section 6)
    streamer/*                                         # T6.2: HoldButton, RiskMeter, AlertFeed, ChatOverlay, CategoryToggle, CaptionsControl, EngineChip, IngestCard
    enrollment/*                                       # T6.2 (adapted camera capture and preview)
    mod/*                                              # T6.3: QueueList, QueueItem, ActionBar, Filters
    report/*                                           # T6.3: Timeline, TotalsGrid, ExportBar
  lib/
    api.ts  ws.ts  auth.ts  types.ts  i18n.ts  utils.ts  format.ts   # T6.2 owns; T6.3 imports only
  public/fonts/                                        # Noto Sans + Noto Sans Devanagari (OFL), self-hosted
  tests/                                               # vitest + Playwright specs (unit specs live beside the code owner)
```
Rule: T6.3 may not edit anything outside `(mod)`, `(report)`, `components/mod`, `components/report`. If it needs a shared change it reports back and the lead creates a follow-up for T6.2's files. Earlier tasks (T0.7, T1.6, T2.5, T3.5, T4.6) run in different waves and may touch any file their wave needs.

## 3. Inputs
REST and WebSocket of `app-api.md` only. The browser never talks to the engine, chat or any platform directly. Types in `lib/types.ts` mirror the wire format of `SafetyEvent`, `RiskSignal`, `ChatMessage`, `ChatVerdict`, `CaptionEvent`, `HoldCommand`, `EngineStatus`, `ModQueueItem` (hand-written to match `contracts.md`; a CI check compares field names against `contracts/tests/wire_samples/*.json`).

## 4. Outputs
User actions to the API (hold, config patch, enrol, session start/stop, mod actions). No data leaves the browser except to the API origin.

## 5. Behaviour

### 5.1 Screens and routes
| Route | Screen | Contents |
|---|---|---|
| `/go-live` (हम लाइव जाएँ) | Setup | Destination toggles (YouTube, Twitch) with key status chips (`configured` / `missing`, never the value); delay slider 3.0–5.0 s (disabled while live); ingest card (SRT/RTMP URLs, Moblin QR via `/api/pairing/*.png`); "Check setup" (calls `POST /api/session/start`) and Start/Stop |
| `/enrol` (चेहरा जोड़ें) | Face allowlist | Live camera (adapted `cameracapture.tsx`), guided 5 poses (front, left, right, up, down) with box overlay from `/api/enrol/preview` every 500 ms, label field, list of enrolled faces with delete, max 8; thumbnails are **not** kept: the list shows label and a generic avatar |
| `/controls` (नियंत्रण) | Categories | One row per video/audio/chat category: enable switch, style (blur/pixelate/solid), sensitivity slider, "mask when unsure" switch; changes apply live via `PATCH /api/config` (optimistic with rollback on 4xx/409) |
| `/live` (लाइव) | Dashboard | Status chip (LIVE / PROTECT / HOLD), **HOLD button**, risk meter, alert feed, chat overlay (allowed messages + "N hidden" counter), captions toggle + language select, engine health (delay, fps, CPU) |
| `/mod` (मॉडरेटर) | Moderator console | Queue (newest first), category chips, approve / delete / timeout (presets 60 s, 600 s, 3600 s) / dismiss, platform-result badge, filters by platform and category, keyboard shortcuts (J/K move, A approve, D delete, T timeout, X dismiss) |
| `/report/[sessionId]` (सुरक्षा रिपोर्ट) | Safety report | Totals grid, event timeline chart (category lanes over time), holds and fail-safe summary, delay/fps, risk peaks, chat counts, export JSON/HTML. No images anywhere |

### 5.2 HOLD button
- Component `HoldButton`: full-width on mobile, fixed to the bottom with `env(safe-area-inset-bottom)`, min height 72 px (96 px on `/live`), ≥ 44×44 px target everywhere else.
- One tap/click or Space/Enter activates hold immediately (no confirmation). Releasing requires a confirm dialog ("Resume streaming? / स्ट्रीम फिर शुरू करें?") to prevent accidental release.
- Shows state by text + icon + colour (never colour alone). Calls `POST /api/hold`; the displayed state comes from the `control.hold` WebSocket echo, not optimistic UI, with a "sending…" state and a 2 s failure toast with retry. The global `H` key toggles hold when focus is not in an input.
- Works if the WebSocket is down (REST only).

### 5.3 Realtime (`lib/ws.ts`)
Native `WebSocket` (not socket.io). Auth frame first, then subscribe; exponential backoff 0.5→8 s with jitter; on reconnect, resubscribe and show a banner "Reconnecting… / फिर से जुड़ रहे हैं…". Each handler is registered once and unregistered on unmount (PrivaStream's `lib/socket.ts` leaked listeners). Heartbeat per `app-api.md`.

### 5.4 State and storage
React context + `useReducer` (no global store library). Token: memory + `sessionStorage` only; removed from the URL fragment on load. `localStorage` holds only UI language and last-selected tab, inside try/catch. Embeddings and frames never touch storage; camera frames are drawn to an off-screen canvas, sent, then the canvas is cleared and the `MediaStream` tracks stopped.

### 5.5 Internationalisation
`lib/i18n.ts` with `{ en, hi }` dictionaries and a header toggle (EN / हिं) persisted in `localStorage`. Initial Hindi labels (extend as needed): Hold → रोकें; Resume → फिर शुरू करें; Risk meter → जोखिम मीटर; Alerts → अलर्ट; Captions → कैप्शन; Faces → चेहरे; Aadhaar/PAN cards → आधार/पैन कार्ड; Number plates → नंबर प्लेट; Signboards → साइनबोर्ड; Moderator → मॉडरेटर; Safety report → सुरक्षा रिपोर्ट; Hidden messages → छिपे हुए संदेश; Go live → लाइव जाएँ. Dates in the user's locale, numbers in Latin digits. Fonts: Noto Sans and Noto Sans Devanagari (OFL) self-hosted in `public/fonts`, `font-display: swap`.

### 5.6 Mobile-first and accessibility
- Baseline viewport 360×640; test 390×844 and 1280×720. No horizontal scroll at 320 px. Single-column on mobile, two-column ≥ 768 px.
- WCAG 2.2 AA: contrast ≥ 4.5:1 (3:1 for large text and UI components), visible `:focus-visible` rings, target size ≥ 24 px (we use ≥ 44 px), no drag-only interactions, consistent help/labels.
- Semantics: landmarks (`header`, `nav`, `main`), one `h1` per page, labelled form controls (react-hook-form + shadcn `Form`), risk meter uses `role="meter"` with `aria-valuenow/min/max` and a text level, alert feed is `role="log"` `aria-live="polite"`, hold state changes use `aria-live="assertive"`, dialogs trap focus and restore it.
- `prefers-reduced-motion`: no animated gauges or transitions. `prefers-color-scheme` supported (dark default on `/live` to reduce glare).
- Keep-awake: `navigator.wakeLock.request('screen')` on `/live` (feature-detected, failure ignored).
- Offline/API-down banner; all buttons that need the API disable with an explanation.

### 5.7 Privacy rules in the UI
Never render or store images of the stream or of cards. Alerts show category, time and confidence only. The streamer role never receives chat text of hidden messages (the API enforces it; the UI also never has a place to show it). The moderator console shows message text only in memory and clears it when an item is resolved.

## 6. Reuse (exact paths under `reference/privastream/src/web/frontend/`)
| Source | Target | Change |
|---|---|---|
| `components/ui/{avatar,badge,button,card,dialog,form,input,label}.tsx`, `lib/utils.ts`, `components.json` | `app/web/components/ui/`, `lib/utils.ts` | Copy as is; add `switch`, `slider`, `tabs`, `toast`, `select` via shadcn CLI. Keep Apache-2.0 headers |
| `tailwind.config.js`, `postcss.config.js`, `app/globals.css`, `tsconfig.json`, `eslint.config.mjs`, `next.config.js`, `package.json` | scaffold files | Versions: Next 16.2.9 (webpack build flag), React 18.3.1, Tailwind 3.4.19, TypeScript 5.9, zod 4, react-hook-form 7.80, @hookform/resolvers 5, lucide-react 0.542. Remove `mediasoup-client` and `socket.io-client`. **UNVERIFIED** that Next 16 accepts React 18 as a peer: verify in T0.7, else use React 19 and re-check the shadcn components |
| `components/enrollment/cameracapture.tsx`, `facepreview.tsx` | `components/enrollment/` | getUserMedia + canvas capture kept; face preview becomes an allowlist card with label and delete; no stored thumbnails |
| `app/enrollment/page.tsx` (flow only) | `(streamer)/enrol/page.tsx` | Keep live-box → capture burst → confirm; change to `/api/enrol/preview` + `/api/enrol`, 5–30 frames (not 150), guided poses |
| `public/pcm-downsampler-worklet.js` | `public/` | Optional mic-level test on `/go-live` |
| Skipped | – | `lib/socket.ts`, `mediasoup-client.ts`, `webrtc.ts`, `video-blur-pipeline.ts`, `audio-redaction-client.ts`, `app/host`, `app/viewer` |

## 7. Edge cases
Token missing/expired (redirect to a "Open the link from the terminal / टर्मिनल का लिंक खोलें" screen); camera permission denied or no camera; browser without `wakeLock`; Hindi text overflow in buttons (allow wrapping, no truncation of the HOLD label); very long chat lines (wrap, max 300 chars shown); 500 alerts/min (virtualised list, cap 200 rendered); two tabs open (second tab shows "Already open" banner but hold works from both); engine lost state; slow network on 4G; phone rotated; mod console with 0 items; report for a session with 0 events.

## 8. Config keys used
Only via API: `categories.*`, `captions.{enabled, language}`, `audio.languages`, `hold.*`, `delay_seconds`, `destinations`. Web env: `NEXT_PUBLIC_API_URL` (default `http://127.0.0.1:8000`), `NEXT_PUBLIC_WS_URL`.

## 9. Test cases
Unit (vitest + testing-library, no browser):
1. `HoldButton`: tap → one `POST /api/hold {active:true}`; release asks for confirmation; Space/Enter work; failure shows retry; state follows the WebSocket echo.
2. `ws.ts`: auth frame sent first; reconnect backoff sequence; handlers unregistered on unmount (no leak after 100 mount/unmount cycles); `hold` events never dropped.
3. `i18n`: every key exists in both `en` and `hi`; toggle persists; missing key falls back to English with a console warning in dev.
4. `api.ts`: masked secrets never rendered; 409 on delay change shows the "restart required" message.
5. `RiskMeter`: `role="meter"` attributes, text level, no animation under reduced motion.
6. Type drift: `lib/types.ts` field names equal the contract wire samples.
Static: `next build`, `eslint`, `tsc --noEmit` clean.
E2E (Playwright against the real API + `FakeEngine`, T6.5), at 360×640 and 1280×720:
7. Fresh load with token fragment → fragment removed → `/go-live` visible.
8. Enrol flow with a fake camera (Chromium `--use-fake-device-for-media-stream`) and fake enroller → face listed, delete works.
9. Toggle a category on `/controls` → API receives the patch, UI reflects the echo.
10. `/live`: inject safety events and risk signals through `FakeEngine` → alert feed and meter update; hold press → HOLD chip and slate state; resume needs confirm.
11. Chat: inject allowed and hidden messages → overlay shows allowed text and "N hidden", streamer never sees hidden text (assert on DOM and on the WebSocket frames).
12. Moderator: queue item → approve/delete/timeout calls hit the API; keyboard shortcuts work.
13. Report page renders from a fixture; export downloads; page has no `<img>` and no canvas data URLs.
14. axe-core scan (`@axe-core/playwright`) on every route: 0 serious/critical violations; keyboard-only run reaches HOLD with Tab in ≤ 6 presses on `/live`.
15. No horizontal overflow at 320 px on every route.
**Acceptance:** T0.7 build green; T1.6 minimal hold page; T2.5 controls + enrol; T3.5 alerts; T4.6 mod console; T6.2/T6.3 all of the above; Gate 6.

## 10. Latency target
Hold tap to request sent ≤ 50 ms; WebSocket event to DOM ≤ 150 ms p95; first contentful paint on `/live` ≤ 2 s on a mid phone over Wi-Fi; JS for `/live` ≤ 250 KB gzipped.

## 11. VRAM / CPU target
No GPU. The camera preview in `/enrol` uses at most 2 fps for preview requests; the page must stay responsive on a mid-range Android phone.

## 12. Tasks
**T0.7** scaffold (deps contracts) → **T1.6** minimal hold page → **T2.5** controls + enrolment → **T3.5** alert feed → **T4.6** mod console + chat overlay → **T6.2** streamer routes + shared layout (deps G5) → **T6.3** mod polish + report (deps T6.2 layout) → **T6.5** Playwright (eval-engineer).

## 13. Out of scope
Viewer-facing pages, WebRTC, native apps, PWA install/offline mode (stretch), landing page and Docker install (stretch, not in CLAUDE.md scope), analytics or telemetry to third parties, accounts/login, light-weight marketing site, in-browser audio capture for streaming.
