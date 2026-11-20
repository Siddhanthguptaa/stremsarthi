/** Browser-side endpoints. Only the API origin is ever contacted (see docs/specs/app-web.md section 4). */

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "")

export const WS_URL =
  process.env.NEXT_PUBLIC_WS_URL ?? `${API_URL.replace(/^http/, "ws")}/api/ws`
