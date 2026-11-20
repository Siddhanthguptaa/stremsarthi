/**
 * Session token handling (docs/specs/app-web.md 5.4).
 *
 * The API prints `http://127.0.0.1:3000/#token=<token>` to its console. The fragment is read once,
 * removed from the address bar, and the token is kept in memory and `sessionStorage` only (never
 * `localStorage`, never a cookie, never a URL query). Storage can be blocked: every access is guarded.
 */

export const TOKEN_STORAGE_KEY = "ss.token"

let memoryToken: string | null = null
const listeners = new Set<() => void>()

/** Subscribe to token changes (for `useSyncExternalStore`). */
export function subscribeToken(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
const notify = () => listeners.forEach((l) => l())

export type TokenStatus = "ready" | "missing"

/**
 * Side-effect free: is there a token to use? Looks at memory, the URL fragment (not yet consumed) and
 * sessionStorage. `bootstrapToken` is the one that consumes the fragment.
 */
export function tokenStatusSnapshot(): TokenStatus {
  if (memoryToken) return "ready"
  if (typeof window === "undefined") return "missing"
  return parseTokenFragment(window.location.hash) || loadStoredToken() ? "ready" : "missing"
}

/** Parse `#token=abc` (also `#a=1&token=abc`). Returns null when absent or empty. */
export function parseTokenFragment(hash: string): string | null {
  const params = new URLSearchParams(hash.startsWith("#") ? hash.slice(1) : hash)
  const token = params.get("token")?.trim()
  return token ? token : null
}

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage
  } catch {
    return null
  }
}

export function loadStoredToken(): string | null {
  if (memoryToken) return memoryToken
  try {
    const stored = storage()?.getItem(TOKEN_STORAGE_KEY) ?? null
    if (stored) memoryToken = stored
    return stored
  } catch {
    return null
  }
}

export function saveToken(token: string): void {
  memoryToken = token
  try {
    storage()?.setItem(TOKEN_STORAGE_KEY, token)
  } catch {
    /* memory only: the token is lost on reload, the user opens the link again */
  }
  notify()
}

export function clearToken(): void {
  memoryToken = null
  try {
    storage()?.removeItem(TOKEN_STORAGE_KEY)
  } catch {
    /* nothing to clear */
  }
  notify()
}

/**
 * Call once on load. Takes a token from the URL fragment (and removes the fragment so it is not
 * copied, bookmarked or sent in a Referer), otherwise falls back to the stored token.
 */
export function bootstrapToken(): string | null {
  if (typeof window === "undefined") return null
  const fromUrl = parseTokenFragment(window.location.hash)
  if (fromUrl) {
    saveToken(fromUrl)
    try {
      // Keep the router's own history state: Next.js stores its tree there.
      window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search)
    } catch {
      window.location.hash = ""
    }
    return fromUrl
  }
  return loadStoredToken()
}
