"""demo_enrol: statuses only, idempotent, one POST through the web enrolment endpoint (mock transport)."""

from __future__ import annotations

import base64
import json

import httpx

from scripts import demo_enrol

JPEG = b"\xff\xd8\xff\xe0fake"


def _client(handler):
    return demo_enrol.make_client("http://api.test", "TOKEN", httpx.MockTransport(handler))


def test_enrols_once_with_streamer_token_and_prints_no_secrets():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.method == "GET":
            return httpx.Response(200, json={"faces": []})
        return httpx.Response(
            201, json={"face_id": "f_abc", "label": demo_enrol.LABEL, "created_at": 1, "expires_at": 2}
        )

    out: list[str] = []
    assert demo_enrol.enrol_demo(_client(handler), lambda: [JPEG] * 6, out.append)
    post = seen[-1]
    assert post.method == "POST" and post.url.path == "/api/enrol"
    assert post.headers["authorization"] == "Bearer TOKEN"
    body = json.loads(post.content)
    assert body["label"] == demo_enrol.LABEL and len(body["frames"]) == 6
    assert base64.b64decode(body["frames"][0]) == JPEG
    assert not any("TOKEN" in line or "frames" in line for line in out)


def test_already_enrolled_is_kept_without_a_post():
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.method)
        return httpx.Response(200, json={"faces": [{"label": demo_enrol.LABEL, "face_id": "f_1"}]})

    out: list[str] = []
    assert demo_enrol.enrol_demo(_client(handler), lambda: 1 / 0, out.append)  # frames never built
    assert calls == ["GET"]


def test_failure_statuses_return_false():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(200, json={"faces": []})
        return httpx.Response(422, json={"error": "too_few_faces", "detail": "x"})

    out: list[str] = []
    assert not demo_enrol.enrol_demo(_client(handler), lambda: [JPEG] * 5, out.append)
    assert out[-1] == "demo enrol: 422 too_few_faces"

    def denied(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    assert not demo_enrol.enrol_demo(_client(denied), lambda: [JPEG], lambda s: None)


def test_engine_allowlist_version_polls_status():
    answers = iter(
        [None, {"d": {"allowlist_version": 0}}, {"t": "engine.status", "v": 1, "d": {"allowlist_version": 2}}]
    )

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"engine": next(answers)})

    assert demo_enrol.engine_allowlist_version(_client(handler), want=1, wait_s=10) == 2


def test_read_token_from_log(tmp_path):
    log = tmp_path / "api.log"
    log.write_text("x\nhttp://127.0.0.1:3000/#token=abc.DEF-1_2\n")
    assert demo_enrol.read_token(log) == "abc.DEF-1_2"
    log.write_text("nothing")
    assert demo_enrol.read_token(log) is None
