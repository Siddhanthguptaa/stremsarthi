import { describe, expect, it, vi } from "vitest"

import { ApiRequestError, createApi } from "@/lib/api"

interface Seen {
  url?: string
  init?: RequestInit
}

function fakeFetch(status: number, body: unknown, seen: Seen = {}) {
  return vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    seen.url = String(url)
    seen.init = init
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })
  }) as unknown as typeof fetch
}

const make = (fetchImpl: typeof fetch) =>
  createApi({ baseUrl: "http://127.0.0.1:8000", getToken: () => "tok-abc", fetchImpl })

const jpeg = (n: number) => new Blob([new Uint8Array([0xff, 0xd8, 0xff, n])], { type: "image/jpeg" })

describe("enrol", () => {
  it("uploads the label and every frame once as multipart, with the bearer token and no JSON content type", async () => {
    const seen: Seen = {}
    const api = make(fakeFetch(201, { face_id: "f1", label: "Asha", created_at: "t", expires_at: null }, seen))
    const frames = Array.from({ length: 8 }, (_, i) => jpeg(i))
    const face = await api.enrol("Asha", frames)
    expect(face.face_id).toBe("f1")
    expect(seen.url).toBe("http://127.0.0.1:8000/api/enrol")
    expect(seen.init?.method).toBe("POST")
    const form = seen.init?.body as FormData
    expect(form).toBeInstanceOf(FormData)
    expect(form.get("label")).toBe("Asha")
    expect(form.getAll("frames")).toHaveLength(8)
    const headers = seen.init?.headers as Record<string, string>
    expect(headers.Authorization).toBe("Bearer tok-abc")
    expect(headers["Content-Type"]).toBeUndefined() // the browser adds multipart/form-data with the boundary
    expect(seen.init?.credentials).toBe("omit")
    expect(seen.url).not.toContain("Asha") // the label never travels in a URL
  })

  it("lists and deletes with the face id encoded in the path", async () => {
    const seen: Seen = {}
    const list = {
      faces: [{ face_id: "f1", label: "Asha", created_at: "t", expires_at: null }],
      max_faces: 8,
      ttl_s: 43200,
      persisted: false,
      min_frames: 5,
      max_frames: 12,
    }
    expect((await make(fakeFetch(200, list, seen)).getEnrolled()).max_faces).toBe(8)
    expect(seen.url).toBe("http://127.0.0.1:8000/api/enrol")
    await make(fakeFetch(200, { deleted: true }, seen)).deleteEnrolled("f/1 x")
    expect(seen.url).toBe("http://127.0.0.1:8000/api/enrol/f%2F1%20x")
    expect(seen.init?.method).toBe("DELETE")
  })

  it("rejects with the API error code", async () => {
    const api = make(fakeFetch(422, { error: "too_few_faces", detail: "x" }))
    await expect(api.enrol("A", [jpeg(1)])).rejects.toMatchObject({ status: 422, code: "too_few_faces" })
  })
})

describe("config", () => {
  it("PATCHes only the changed fields as JSON", async () => {
    const seen: Seen = {}
    const api = make(fakeFetch(200, { categories: {} }, seen))
    await api.patchConfig({ categories: { face: { sensitivity: 0.7 } } })
    expect(seen.init?.method).toBe("PATCH")
    expect(JSON.parse(String(seen.init?.body))).toEqual({ categories: { face: { sensitivity: 0.7 } } })
    expect((seen.init?.headers as Record<string, string>)["Content-Type"]).toBe("application/json")
  })

  it("surfaces a 409 requires_restart as a typed error", async () => {
    const api = make(fakeFetch(409, { error: "requires_restart", detail: "delay_seconds cannot change" }))
    const err = (await api.patchConfig({ delay_seconds: 4 }).catch((e: unknown) => e)) as ApiRequestError
    expect(err).toBeInstanceOf(ApiRequestError)
    expect(err.status).toBe(409)
    expect(err.code).toBe("requires_restart")
  })

  it("gets the config", async () => {
    const seen: Seen = {}
    await make(fakeFetch(200, { categories: {} }, seen)).getConfig()
    expect(seen.url).toBe("http://127.0.0.1:8000/api/config")
    expect(seen.init?.method).toBe("GET")
  })
})
