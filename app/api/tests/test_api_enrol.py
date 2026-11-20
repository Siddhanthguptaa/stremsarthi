"""Enrolment endpoints, allowlist publishing and the engine resync (fake enroller, in-process bus)."""

from __future__ import annotations

import base64
import dataclasses
import gc
import io
import logging
import random
import stat
import tempfile
import weakref
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api.main import create_app
from contracts import AllowlistUpdate, Config, EnrolConfig, EnrolledFaceMeta, Topic
from contracts.fakes import FakeEnroller


def jpeg(seed: int = 0, comment: bytes | None = None, size: tuple[int, int] = (64, 48)) -> bytes:
    rng = random.Random(seed)
    im = Image.frombytes("RGB", size, bytes(rng.randrange(256) for _ in range(size[0] * size[1] * 3)))
    buf = io.BytesIO()
    kwargs: dict[str, Any] = {"comment": comment} if comment else {}
    im.save(buf, "JPEG", **kwargs)
    return buf.getvalue()


def frames_json(label: str, n: int = 8, **kw: Any) -> dict[str, Any]:
    return {"label": label, "frames": [base64.b64encode(jpeg(i, **kw)).decode() for i in range(n)]}


@pytest.fixture
def app(bus, settings_factory):
    settings = settings_factory(enrol_rate_limit=100, allowlist_resync_grace_s=0.0)
    app = create_app(settings, bus, Config(_env_file=None))
    app.state.enroller = FakeEnroller()
    return app


def allowlist_events(bus) -> list[AllowlistUpdate]:
    return [e for t, e in bus.bus.history if t is Topic.ALLOWLIST]


def streamer_client(app):
    c = TestClient(app, base_url="http://127.0.0.1:8000")
    return c, {"Authorization": f"Bearer {app.state.tokens.issue('streamer')}"}


def test_enrol_json_stores_publishes_one_add_and_never_returns_the_embedding(client, auth, bus, app):
    r = client.post("/api/enrol", json=frames_json("Asha"), headers=auth)
    assert r.status_code == 201
    body = r.json()
    assert set(body) == {"face_id", "label", "created_at", "expires_at"}
    assert body["label"] == "Asha" and body["expires_at"] is not None
    EnrolledFaceMeta(**body)  # valid contract
    assert "embedding" not in r.text

    events = allowlist_events(bus)
    assert len(events) == 1
    ev = events[0]
    assert ev.op == "add" and ev.face_id == body["face_id"] and ev.label == "Asha"
    assert ev.embedding is not None and len(ev.embedding) == 16
    assert ev.version == app.state.allowlist.store.version == 1


def test_enrol_multipart_with_repeated_frames_parts(client, auth, bus):
    files = [("frames", (f"f{i}.jpg", jpeg(i), "image/jpeg")) for i in range(6)]
    r = client.post("/api/enrol", data={"label": "Ravi"}, files=files, headers=auth)
    assert r.status_code == 201, r.text
    assert r.json()["label"] == "Ravi"
    assert [e.op for e in allowlist_events(bus)] == ["add"]


def test_enrol_loads_the_enroller_from_enrol_impl_at_request_time(bus, settings_factory):
    config = Config(_env_file=None, enrol=EnrolConfig(impl="contracts.fakes:FakeEnroller"))
    app = create_app(settings_factory(), bus, config)  # no injected enroller
    c, h = streamer_client(app)
    with c:
        assert c.post("/api/enrol", json=frames_json("Meera"), headers=h).status_code == 201


@pytest.mark.parametrize(
    "impl", ["no_such_package.faces:Enroller", "contracts.fakes:NoSuchClass", "bad impl"]
)
def test_enroller_unavailable_is_503(bus, settings_factory, impl):
    config = Config(_env_file=None, enrol=EnrolConfig(impl=impl))
    app = create_app(settings_factory(), bus, config)
    c, h = streamer_client(app)
    with c:
        r = c.post("/api/enrol", json=frames_json("Meera"), headers=h)
        assert r.status_code == 503 and r.json()["error"] == "enroller_unavailable"
    assert allowlist_events(bus) == []


def test_default_impl_is_the_vision_enroller():
    assert Config(_env_file=None).enrol.impl == "vision.detectors.faces.enroll:Enroller"


@pytest.mark.parametrize("n,code", [(3, "too_few_frames"), (13, "too_many_frames")])
def test_frame_count_limits(client, auth, bus, n, code):
    r = client.post("/api/enrol", json=frames_json("Asha", n), headers=auth)
    assert r.status_code == 422 and r.json()["error"] == code
    assert allowlist_events(bus) == []


def test_min_frames_comes_from_the_config(bus, settings_factory):
    config = Config(_env_file=None, enrol=EnrolConfig(min_frames=7))
    app = create_app(settings_factory(), bus, config)
    app.state.enroller = FakeEnroller()
    c, h = streamer_client(app)
    with c:
        assert c.post("/api/enrol", json=frames_json("A", 6), headers=h).json()["error"] == "too_few_frames"
        assert c.post("/api/enrol", json=frames_json("A", 7), headers=h).status_code == 201


def test_enroller_that_finds_no_face_is_422_too_few_faces(client, auth, app, bus):
    class NoFaces:
        def enrol(self, images, label):
            raise ValueError("no usable face")

    app.state.enroller = NoFaces()
    r = client.post("/api/enrol", json=frames_json("Asha"), headers=auth)
    assert r.status_code == 422 and r.json()["error"] == "too_few_faces"
    assert allowlist_events(bus) == [] and len(app.state.allowlist.store) == 0


def test_enroller_crash_is_a_clean_500_without_details(client, auth, app):
    class Boom:
        def enrol(self, images, label):
            raise RuntimeError("model file /secret/path missing")

    app.state.enroller = Boom()
    r = client.post("/api/enrol", json=frames_json("Asha"), headers=auth)
    assert r.status_code == 500 and r.json()["error"] == "enrol_failed"
    assert "secret" not in r.text


@pytest.mark.parametrize("label", ["", "x" * 41, "bad\x07label", None])
def test_bad_labels_are_rejected(client, auth, label):
    payload: dict[str, Any] = {"frames": frames_json("x")["frames"]}
    if label is not None:
        payload["label"] = label
    r = client.post("/api/enrol", json=payload, headers=auth)
    assert r.status_code == 422 and r.json()["error"] == "invalid_label"


def test_non_jpeg_and_garbage_frames_are_rejected(client, auth):
    png = io.BytesIO()
    Image.new("RGB", (8, 8)).save(png, "PNG")
    bad = {"label": "A", "frames": [base64.b64encode(png.getvalue()).decode()] * 6}
    assert client.post("/api/enrol", json=bad, headers=auth).json()["error"] == "bad_frame"
    notb64 = {"label": "A", "frames": ["!!!not base64!!!"] * 6}
    assert client.post("/api/enrol", json=notb64, headers=auth).json()["error"] == "bad_frame"
    truncated = {"label": "A", "frames": [base64.b64encode(jpeg(1)[:60]).decode()] * 6}
    assert client.post("/api/enrol", json=truncated, headers=auth).status_code == 422
    huge = {"label": "A", "frames": [base64.b64encode(jpeg(1)).decode()] * 5}
    huge["frames"][0] = base64.b64encode(b"\xff\xd8\xff" + b"0" * (1024 * 1024 + 10)).decode()
    assert client.post("/api/enrol", json=huge, headers=auth).json()["error"] == "frame_too_large"
    r = client.post("/api/enrol", content=b"x", headers={**auth, "Content-Type": "text/plain"})
    assert r.status_code == 415


def test_body_over_12_mb_is_refused(client, auth):
    big = b"0" * (12 * 1024 * 1024 + 1)
    r = client.post("/api/enrol", content=big, headers={**auth, "Content-Type": "application/json"})
    assert r.status_code == 413


def test_ninth_face_is_refused_and_eight_are_accepted(client, auth, bus, app):
    assert app.state.allowlist.store.max_faces == 8
    for i in range(8):
        assert client.post("/api/enrol", json=frames_json(f"Person {i}"), headers=auth).status_code == 201
    r = client.post("/api/enrol", json=frames_json("Person 9"), headers=auth)
    assert r.status_code == 422 and r.json()["error"] == "allowlist_full"
    assert len(allowlist_events(bus)) == 8
    assert len(client.get("/api/enrol", headers=auth).json()["faces"]) == 8


def test_max_faces_comes_from_the_config(bus, settings_factory):
    config = Config(_env_file=None, enrol=EnrolConfig(max_faces=2))
    app = create_app(settings_factory(enrol_rate_limit=50), bus, config)
    app.state.enroller = FakeEnroller()
    c, h = streamer_client(app)
    with c:
        codes = [c.post("/api/enrol", json=frames_json(f"P{i}"), headers=h).status_code for i in range(3)]
        assert codes == [201, 201, 422]


def test_list_returns_metas_only(client, auth):
    client.post("/api/enrol", json=frames_json("Asha"), headers=auth)
    r = client.get("/api/enrol", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["max_faces"] == 8 and body["ttl_s"] == 43_200 and body["persisted"] is False
    assert body["min_frames"] == 5 and body["max_frames"] == 12
    assert [set(f) for f in body["faces"]] == [{"face_id", "label", "created_at", "expires_at"}]
    assert "embedding" not in r.text


def test_delete_publishes_remove_and_unknown_is_404(client, auth, bus):
    face_id = client.post("/api/enrol", json=frames_json("Asha"), headers=auth).json()["face_id"]
    r = client.delete(f"/api/enrol/{face_id}", headers=auth)
    assert r.status_code == 200 and r.json() == {"deleted": True}
    ops = allowlist_events(bus)
    assert [e.op for e in ops] == ["add", "remove"]
    assert ops[1].face_id == face_id and ops[1].embedding is None and ops[1].version == 2
    assert client.get("/api/enrol", headers=auth).json()["faces"] == []
    assert client.delete(f"/api/enrol/{face_id}", headers=auth).status_code == 404
    assert client.delete("/api/enrol/bad%20id", headers=auth).status_code == 404


def test_ttl_expiry_removes_the_face_and_tells_the_engine(client, auth, bus, app):
    store = app.state.allowlist.store
    now = [store._clock()]
    store._clock = lambda: now[0]
    face_id = client.post("/api/enrol", json=frames_json("Asha"), headers=auth).json()["face_id"]
    now[0] += store.ttl_s + 1
    assert client.get("/api/enrol", headers=auth).json()["faces"] == []
    ops = allowlist_events(bus)
    assert [e.op for e in ops] == ["add", "remove"] and ops[1].face_id == face_id


@pytest.mark.parametrize("role", ["moderator", "hotkey", "viewer-readonly"])
def test_only_the_streamer_may_enrol(client, role_headers, role):
    h = role_headers(role)
    assert client.post("/api/enrol", json=frames_json("A"), headers=h).status_code == 403
    assert client.get("/api/enrol", headers=h).status_code == 403
    assert client.delete("/api/enrol/f1", headers=h).status_code == 403


def test_enrol_requires_a_token(client):
    assert client.post("/api/enrol", json=frames_json("A")).status_code == 401
    assert client.get("/api/enrol").status_code == 401
    assert client.delete("/api/enrol/f1").status_code == 401


def test_enrol_is_rate_limited_and_concurrency_guarded(bus, settings_factory):
    app = create_app(settings_factory(enrol_rate_limit=3), bus, Config(_env_file=None))
    app.state.enroller = FakeEnroller()
    c, h = streamer_client(app)
    with c:
        codes = [c.post("/api/enrol", json=frames_json(f"P{i}"), headers=h).status_code for i in range(4)]
        assert codes == [201, 201, 201, 429]
    app = create_app(settings_factory(enrol_rate_limit=50), bus, Config(_env_file=None))
    app.state.enroller = FakeEnroller()
    c, h = streamer_client(app)
    with c:
        app.state.enrol_inflight = 2
        r = c.post("/api/enrol", json=frames_json("A"), headers=h)
        assert r.status_code == 429 and r.json()["error"] == "busy"


def test_inflight_counter_returns_to_zero_after_errors(client, auth, app):
    client.post("/api/enrol", json=frames_json("A", 2), headers=auth)
    client.post("/api/enrol", json=frames_json("A"), headers=auth)
    assert app.state.enrol_inflight == 0


def test_bus_failure_rolls_the_face_back(client, auth, app, bus, monkeypatch):
    def broken(topic, event):
        raise RuntimeError("bus down")

    monkeypatch.setattr(bus, "publish", broken)
    r = client.post("/api/enrol", json=frames_json("Asha"), headers=auth)
    assert r.status_code == 503 and r.json()["error"] == "bus_unavailable"
    assert len(app.state.allowlist.store) == 0


# --------------------------------------------------------------------------- resync
def engine_status(sample, version: int):
    return dataclasses.replace(sample("EngineStatus"), allowlist_version=version)


def test_engine_with_an_older_allowlist_version_gets_a_full_snapshot(
    client, auth, app, bus, publish, wait_until, sample
):
    for name in ("Asha", "Ravi"):
        client.post("/api/enrol", json=frames_json(name), headers=auth)
    bus.bus.history.clear()
    assert app.state.allowlist.store.version == 2

    publish(Topic.ENGINE_STATUS, engine_status(sample, 0))  # engine restarted: knows nothing
    assert wait_until(lambda: len(allowlist_events(bus)) == 3)
    ops = allowlist_events(bus)
    assert [e.op for e in ops] == ["snapshot", "add", "add"]
    assert {e.version for e in ops} == {2}
    assert {e.label for e in ops[1:]} == {"Asha", "Ravi"}
    assert all(e.embedding for e in ops[1:])

    # Another stale status right away does not flood the bus (rate limited); a matching one is quiet.
    publish(Topic.ENGINE_STATUS, engine_status(sample, 0))
    publish(Topic.ENGINE_STATUS, engine_status(sample, 2))
    client.get("/api/health")
    assert wait_until(lambda: app.state.hub.last_status is not None)
    assert len(allowlist_events(bus)) == 3
    assert app.state.allowlist.resyncs == 1


def test_up_to_date_engine_gets_nothing(client, auth, app, bus, publish, sample):
    client.post("/api/enrol", json=frames_json("Asha"), headers=auth)
    bus.bus.history.clear()
    publish(Topic.ENGINE_STATUS, engine_status(sample, 1))
    client.get("/api/health")
    assert allowlist_events(bus) == []


def test_status_right_after_a_change_is_not_treated_as_stale(bus, settings_factory, sample):
    app = create_app(settings_factory(allowlist_resync_grace_s=60.0), bus, Config(_env_file=None))
    app.state.enroller = FakeEnroller()
    c, h = streamer_client(app)
    with c:
        c.post("/api/enrol", json=frames_json("Asha"), headers=h)
        c.portal.call(bus.publish, Topic.ENGINE_STATUS, engine_status(sample, 0))  # type: ignore[union-attr]
        c.get("/api/health")
        assert [e.op for e in allowlist_events(bus)] == ["add"]


def test_engine_with_faces_the_api_no_longer_has_gets_an_empty_snapshot(
    client, app, bus, publish, wait_until, sample
):
    publish(Topic.ENGINE_STATUS, engine_status(sample, 5))
    assert wait_until(lambda: len(allowlist_events(bus)) == 1)
    ev = allowlist_events(bus)[0]
    assert ev.op == "snapshot" and ev.version == 0


# --------------------------------------------------------------------------- memory only, logs
def test_frames_live_only_in_memory_during_the_request(client, auth, app, tmp_path, monkeypatch):
    marker = b"FRAME-MARKER-9f2c"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    seen: list[Any] = []

    class Spy:
        def enrol(self, images, label):
            seen.extend(weakref.ref(img) for img in images)
            assert all(img.ndim == 3 and img.shape[2] == 3 for img in images)  # BGR arrays
            return FakeEnroller().enrol(images, label)

    app.state.enroller = Spy()
    r = client.post("/api/enrol", json=frames_json("Asha", comment=marker), headers=auth)
    assert r.status_code == 201
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []

    files = [("frames", (f"f{i}.jpg", jpeg(i, comment=marker), "image/jpeg")) for i in range(6)]
    r = client.post("/api/enrol", data={"label": "Ravi"}, files=files, headers=auth)
    assert r.status_code == 201
    assert [p for p in tmp_path.rglob("*") if p.is_file()] == []  # no spooled upload either

    gc.collect()
    assert len(seen) == 14 and all(ref() is None for ref in seen), "frames were retained after the request"
    assert marker not in r.content and marker not in client.get("/api/enrol", headers=auth).content


def test_enrolment_leaks_no_label_embedding_token_or_frame_into_logs(
    client, auth, app, bus, caplog, capfd, streamer_token, secret
):
    caplog.set_level(logging.DEBUG)
    label = "Zxqv-Unique-Name"
    r = client.post("/api/enrol", json=frames_json(label, comment=b"FRAME-MARKER-77"), headers=auth)
    face_id = r.json()["face_id"]
    embedding = allowlist_events(bus)[0].embedding
    client.get("/api/enrol", headers=auth)
    client.post("/api/enrol", json=frames_json(label, 2), headers=auth)  # error path

    class Crash:
        def enrol(self, images, label):
            raise RuntimeError(label)

    app.state.enroller = Crash()
    client.post("/api/enrol", json=frames_json(label), headers=auth)  # enroller crash path
    client.delete(f"/api/enrol/{face_id}", headers=auth)
    app.state.allowlist.resync()

    captured = caplog.text + "".join(capfd.readouterr())
    assert label not in captured
    assert "FRAME-MARKER-77" not in captured
    assert streamer_token not in captured and secret not in captured
    assert embedding is not None
    for value in embedding:
        assert f"{value:.6f}"[:8] not in captured and repr(value) not in captured


# --------------------------------------------------------------------------- persistence (opt-in)
def persisted_app(bus, settings_factory, tmp_path: Path, key: str | None = "unit-test-passphrase"):
    path = tmp_path / "home" / "allowlist.enc"
    settings = settings_factory(allowlist_path=str(path), allowlist_key=key, enrol_rate_limit=50)
    config = Config(_env_file=None, enrol=EnrolConfig(persist=True))
    app = create_app(settings, bus, config)
    app.state.enroller = FakeEnroller()
    return app, path


def test_persistence_is_off_by_default_so_enrolling_writes_no_file(client, auth, app, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    client.post("/api/enrol", json=frames_json("Asha"), headers=auth)
    assert not app.state.allowlist.store.persistent
    assert list(tmp_path.rglob("*")) == []


def test_persist_on_writes_an_encrypted_0600_file_and_survives_a_restart(bus, settings_factory, tmp_path):
    app, path = persisted_app(bus, settings_factory, tmp_path)
    c, h = streamer_client(app)
    with c:
        r = c.post("/api/enrol", json=frames_json("Persisted-Person"), headers=h)
        assert r.status_code == 201 and r.json()["expires_at"] is None
        assert c.get("/api/enrol", headers=h).json()["persisted"] is True
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert b"Persisted-Person" not in path.read_bytes()

    app2, _ = persisted_app(bus, settings_factory, tmp_path)
    c, h = streamer_client(app2)
    with c:
        faces = c.get("/api/enrol", headers=h).json()["faces"]
        assert [f["label"] for f in faces] == ["Persisted-Person"]
        assert c.delete(f"/api/enrol/{faces[0]['face_id']}", headers=h).status_code == 200
    app3, _ = persisted_app(bus, settings_factory, tmp_path)
    assert len(app3.state.allowlist.store) == 0


def test_persist_on_without_a_key_never_writes(bus, settings_factory, tmp_path):
    app, path = persisted_app(bus, settings_factory, tmp_path, key=None)
    c, h = streamer_client(app)
    with c:
        assert c.post("/api/enrol", json=frames_json("Asha"), headers=h).status_code == 201
        assert c.get("/api/enrol", headers=h).json()["persisted"] is False
    assert not path.exists() and not path.parent.exists()


def test_allowlist_key_is_read_from_the_environment_only(monkeypatch):
    from app.api.settings import ApiSettings

    assert ApiSettings(_env_file=None, secret_key="s").allowlist_key is None  # type: ignore[call-arg]
    monkeypatch.setenv("ALLOWLIST_KEY", "from-env")
    s = ApiSettings(_env_file=None, secret_key="s")  # type: ignore[call-arg]
    assert s.allowlist_key is not None and s.allowlist_key.get_secret_value() == "from-env"
    assert "from-env" not in repr(s)
