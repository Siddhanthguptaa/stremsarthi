"""Ingest reader tests on the CPU with synthetic frames and offline NUT files (no network)."""

from __future__ import annotations

import threading
import time

import av
import numpy as np
import pytest

from contracts.enums import Category, HoldSource, Modality, Topic
from contracts.fakes.bus import InProcessBus
from contracts.media import Frame
from contracts.runtime import FrameSink, FrameSource
from relay.ingest import (
    AUDIO_BLOCK_SAMPLES,
    AudioBlock,
    AudioBlocker,
    DropOldestQueue,
    IngestFrame,
    NetworkInput,
    frame_to_canvas,
    letterbox_geometry,
    letterbox_image,
)
from relay.sink import NullFrameSink
from relay.tests.support_media import write_nut

FILE_OPTIONS: dict[str, str] = {}


# --------------------------------------------------------------------------- letterbox
@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ((1280, 720), (1280, 720, 0, 0)),
        ((1920, 1080), (1280, 720, 0, 0)),
        ((640, 480), (960, 720, 160, 0)),  # 4:3 is pillarboxed
        ((720, 1280), (405, 720, 437, 0)),  # 9:16 portrait is pillarboxed
        ((1080, 1920), (405, 720, 437, 0)),
        ((1280, 960), (960, 720, 160, 0)),
        ((1280, 400), (1280, 400, 0, 160)),  # ultra-wide is letterboxed
        ((160, 90), (1280, 720, 0, 0)),  # small 16:9 is scaled up
    ],
)
def test_letterbox_geometry_preserves_aspect(src, expected):
    w, h, x, y = letterbox_geometry(*src)
    assert (w, h, x, y) == expected
    assert x * 2 + w == 1280 or abs(x * 2 + w - 1280) <= 1
    assert abs(w / h - src[0] / src[1]) < 0.01


@pytest.mark.parametrize(
    "size", [(640, 480), (720, 1280), (1080, 1920), (1280, 720), (1920, 1080), (321, 123)]
)
def test_letterbox_image_pads_black_and_keeps_the_picture_unstretched(size):
    width, height = size
    image = np.full((height, width, 3), 255, np.uint8)
    canvas = letterbox_image(image)
    assert canvas.shape == (720, 1280, 3) and canvas.dtype == np.uint8
    w, h, x, y = letterbox_geometry(width, height)
    assert canvas[y : y + h, x : x + w].min() >= 250, "the picture fills exactly the fitted rectangle"
    padded = canvas.copy()
    padded[y : y + h, x : x + w] = 0
    assert padded.max() == 0, "everything outside the fitted rectangle is black"


def test_letterbox_does_not_stretch_a_circle():
    import cv2

    image = np.zeros((480, 640, 3), np.uint8)  # 4:3
    cv2.circle(image, (320, 240), 200, (255, 255, 255), -1)
    canvas = letterbox_image(image)
    ys, xs = np.nonzero(canvas[:, :, 0] > 128)
    assert abs((xs.max() - xs.min()) - (ys.max() - ys.min())) <= 3, "a circle must stay a circle"


@pytest.mark.parametrize("size", [(640, 480), (720, 1280), (1080, 1920), (1280, 720)])
def test_frame_to_canvas_matches_the_reference_letterbox(size):
    width, height = size
    image = np.full((height, width, 3), 200, np.uint8)
    frame = av.VideoFrame.from_ndarray(image, format="bgr24")
    canvas = frame_to_canvas(frame)
    assert canvas.shape == (720, 1280, 3)
    w, h, x, y = letterbox_geometry(width, height)
    assert canvas[y : y + h, x : x + w].mean() > 190
    outside = canvas.copy()
    outside[y : y + h, x : x + w] = 0
    assert outside.max() == 0


def test_ingest_frame_is_a_frame_with_a_generation():
    frame = IngestFrame(3, 0.1, 5.0, np.zeros((720, 1280, 3), np.uint8), generation=2)
    assert isinstance(frame, Frame) and frame.generation == 2
    frame.validate_canvas()


def test_audio_block_unpacks_as_the_two_tuple_the_protocol_promises():
    block = AudioBlock(1920, np.zeros(960, np.int16), generation=4)
    index, pcm = block
    assert index == 1920 and len(pcm) == 960 and block.generation == 4


# --------------------------------------------------------------------------- queue
def test_drop_oldest_queue_keeps_the_newest_and_counts_evictions():
    queue = DropOldestQueue(3)
    evicted = [queue.put(i) for i in range(5)]
    assert evicted == [0, 0, 0, 1, 1] and queue.dropped == 2 and queue.high_water == 3
    assert [queue.get(0) for _ in range(3)] == [2, 3, 4]
    assert queue.get(0.01) is None


def test_drop_oldest_queue_get_wakes_on_put_and_on_close():
    queue = DropOldestQueue(2)
    got: list = []
    thread = threading.Thread(target=lambda: got.append(queue.get(5)))
    thread.start()
    time.sleep(0.05)
    queue.put("x")
    thread.join(2)
    assert got == ["x"]
    waiter = threading.Thread(target=lambda: got.append(queue.get(5)))
    waiter.start()
    time.sleep(0.05)
    queue.close()
    waiter.join(2)
    assert got == ["x", None] and not waiter.is_alive()
    assert queue.put("y") == 0 and len(queue) == 0, "a closed queue accepts nothing"


# --------------------------------------------------------------------------- audio blocker
def _ramp(start, n):
    return (np.arange(start, start + n) % 30000).astype(np.int16)


def test_audio_blocker_emits_contiguous_fixed_blocks_with_absolute_indices():
    blocker = AudioBlocker()
    out = []
    position = 0
    for size in (1024, 1024, 700, 1500, 333):
        out += blocker.push(position / 48000, _ramp(position, size))
        position += size
    assert [index for index, _ in out] == [i * AUDIO_BLOCK_SAMPLES for i in range(len(out))]
    joined = np.concatenate([pcm for _, pcm in out])
    assert np.array_equal(joined, _ramp(0, len(joined))), "no sample lost, duplicated or reordered"


def test_audio_blocker_starts_at_the_rebased_index_of_the_first_sample():
    blocker = AudioBlocker()
    out = blocker.push(0.5, _ramp(0, 4800))
    assert out[0][0] == 24000


def test_audio_blocker_trims_samples_before_the_clock_origin():
    blocker = AudioBlocker()
    out = blocker.push(-0.01, _ramp(0, 1920))  # first 480 samples are before time zero
    assert out[0][0] == 0 and blocker.samples_trimmed == 480
    assert np.array_equal(out[0][1], _ramp(480, 960))


def test_audio_blocker_fills_a_gap_with_silence_so_indices_stay_on_the_clock():
    blocker = AudioBlocker()
    out = blocker.push(0.0, _ramp(0, 960))
    out += blocker.push(0.1, _ramp(0, 960))  # 4800 samples start; 3840 missing
    assert blocker.silence_inserted == 3840
    assert [index for index, _ in out] == [0, 960, 1920, 2880, 3840, 4800]
    assert not out[2][1].any() and not out[4][1].any()
    assert out[5][1].any()


def test_audio_blocker_trims_overlap_and_ignores_small_jitter():
    blocker = AudioBlocker()
    blocker.push(0.0, _ramp(0, 1920))
    out = blocker.push((1920 + 96) / 48000, _ramp(1920, 960))  # 2 ms late: jitter, not a gap
    assert blocker.samples_trimmed == 0 and blocker.silence_inserted == 0
    assert [index for index, _ in out] == [1920]
    blocker2 = AudioBlocker()
    blocker2.push(0.0, _ramp(0, 1920))
    blocker2.push(960 / 48000, _ramp(0, 1920))  # starts 960 samples before the end of what we have
    assert blocker2.samples_trimmed == 960


def test_audio_blocker_jumps_over_a_gap_beyond_the_cap():
    blocker = AudioBlocker(max_gap_s=0.5)
    out = blocker.push(0.0, _ramp(0, 1000))
    out += blocker.push(10.0, _ramp(0, 2000))
    indices = [index for index, _ in out]
    assert indices == [0, 960, 480000, 480960], "the partial block is flushed padded, then indices jump"
    assert blocker.silence_inserted == 0


# --------------------------------------------------------------------------- reader over a file
def _collect(source: NetworkInput, *, until_generation=1, timeout=6.0):
    frames: list[IngestFrame] = []
    audio: list[AudioBlock] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        item = source._video.get(0.05)
        if item is not None:
            frames.append(item)
        while (block := source._audio.get(0)) is not None:
            audio.append(block)
        if source.generation >= until_generation and source.error_code == "input-eof" and item is None:
            break
    while (block := source._audio.get(0)) is not None:
        audio.append(block)
    return frames, audio


def test_reader_rebases_pts_to_zero_letterboxes_reads_fps_and_tags_audio(tmp_path):
    path = str(tmp_path / "in.nut")
    write_nut(path, n_frames=20, size=(160, 120), fps=25, start_s=3.0)
    source = NetworkInput(path, fps=30, container_options=FILE_OPTIONS, queue_size=64)
    try:
        frames, audio = _collect(source)
    finally:
        source.close()
    gen1 = [f for f in frames if f.generation == 1]
    assert len(gen1) == 20
    assert gen1[0].pts == 0.0, "PTS is rebased to 0 at the first frame of the connection"
    assert all(abs(f.pts - i / 25) < 1e-3 for i, f in enumerate(gen1))
    assert [f.seq for f in gen1] == list(range(20))
    assert all(f.image.shape == (720, 1280, 3) for f in gen1)
    assert gen1[0].image[360, 640].min() >= 38 and gen1[0].image[360, 640].max() <= 42, (
        "picture content survives"
    )
    assert source.fps == 25.0 and source.samples_per_frame == 1920.0, (
        "rate comes from the stream, not a constant"
    )
    gen1_audio = [a for a in audio if a.generation == 1]
    assert gen1_audio[0][0] == 0, "audio starts at the same zero as video"
    assert [a[0] for a in gen1_audio] == [i * AUDIO_BLOCK_SAMPLES for i in range(len(gen1_audio))]
    assert all(a[1].dtype == np.int16 and a[1].shape == (AUDIO_BLOCK_SAMPLES,) for a in gen1_audio)


def test_reader_keeps_pts_strictly_monotonic_and_drops_replayed_timestamps(tmp_path):
    path = str(tmp_path / "in.nut")
    write_nut(path, n_frames=15, size=(64, 48), fps=25, audio=False)
    source = NetworkInput(path, container_options=FILE_OPTIONS, queue_size=64)
    try:
        frames, _ = _collect(source)
    finally:
        source.close()
    for generation in {f.generation for f in frames}:
        pts = [f.pts for f in frames if f.generation == generation]
        assert all(b > a for a, b in zip(pts, pts[1:], strict=False))


def test_reader_reconnects_with_a_new_generation_and_restarts_the_clock(tmp_path):
    path = str(tmp_path / "in.nut")
    write_nut(path, n_frames=10, size=(64, 48), fps=25, start_s=2.0)
    source = NetworkInput(path, container_options=FILE_OPTIONS, queue_size=64)
    try:
        frames, audio = _collect(source, until_generation=2, timeout=6)
        deadline = time.monotonic() + 6
        while source.generation < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        more, more_audio = _collect(source, until_generation=2, timeout=3)
        frames += more
        audio += more_audio
    finally:
        source.close()
    generations = sorted({f.generation for f in frames})
    assert generations[:2] == [1, 2]
    second = [f for f in frames if f.generation == 2]
    assert second and second[0].pts == 0.0, "PTS restarts at 0 in the new generation"
    assert second[0].seq > max(f.seq for f in frames if f.generation == 1), (
        "seq keeps counting across generations"
    )
    assert any(a.generation == 2 and a[0] == 0 for a in audio), "the audio sample index restarts too"
    assert source.failure_counts["input-eof"] >= 1


def test_close_stops_threads_iterators_and_forgets_the_url(tmp_path):
    path = str(tmp_path / "in.nut")
    write_nut(path, n_frames=5, size=(64, 48), fps=25)
    before = {t.name for t in threading.enumerate()}
    source = NetworkInput(path, container_options=FILE_OPTIONS)
    iterator = source.frames()
    first = next(iterator)
    assert isinstance(first, Frame)
    source.close()
    source.close()  # idempotent
    assert list(iterator) == [], "frames() ends once the source is closed"
    assert list(source.audio_blocks()) == []
    assert source._url is None
    assert {t.name for t in threading.enumerate()} - before == set()


def test_close_hooks_run_once(tmp_path):
    path = str(tmp_path / "in.nut")
    write_nut(path, n_frames=3, size=(64, 48), fps=25)
    calls = []
    source = NetworkInput(path, container_options=FILE_OPTIONS)
    source.add_close_hook(lambda: calls.append(1))
    source.close()
    source.close()
    assert calls == [1]


def test_source_satisfies_the_frame_source_protocol(tmp_path):
    source = NetworkInput(str(tmp_path / "missing.nut"), container_options=FILE_OPTIONS, start=False)
    assert isinstance(source, FrameSource)
    assert isinstance(NullFrameSink(), FrameSink)


def test_a_missing_input_never_leaks_the_url_or_credentials():
    secret = "SYNTHETIC-PRIVATE-PASSWORD"
    url = f"rtsp://user:{secret}@127.0.0.1:1/camera"
    source = NetworkInput(url, open_timeout=0.2, read_timeout=0.2)
    try:
        deadline = time.monotonic() + 3
        while source.error_code is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert source.error_code == "input-open"
        blob = repr(source) + str(source.last_failure) + str(source.stats()) + str(list(source.drop_events))
        assert secret not in blob and "127.0.0.1" not in blob
    finally:
        source.close()


def test_reader_creates_no_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    media = tmp_path / "media"
    media.mkdir()
    write_nut(str(media / "in.nut"), n_frames=10, size=(64, 48), fps=25)
    before = {p for p in tmp_path.rglob("*")}
    source = NetworkInput(str(media / "in.nut"), container_options=FILE_OPTIONS)
    try:
        next(iter(source.frames()))
    finally:
        source.close()
    assert {p for p in tmp_path.rglob("*")} == before


def test_every_decoded_frame_emits_an_ingest_decode_timing(tmp_path):
    path = str(tmp_path / "in.nut")
    write_nut(path, n_frames=12, size=(64, 48), fps=25, audio=False)
    bus = InProcessBus()
    source = NetworkInput(path, container_options=FILE_OPTIONS, bus=bus, queue_size=64)
    try:
        frames, _ = _collect(source)
    finally:
        source.close()
    timings = [e for e in bus.of(Topic.TIMING) if e.stage == "ingest_decode"]
    assert len(timings) >= len([f for f in frames if f.generation == 1]) > 0
    assert all(t.t_end >= t.t_start and t.pts is not None for t in timings)
    assert [t.seq for t in timings[:5]] == [0, 1, 2, 3, 4]


# --------------------------------------------------------------------------- hold (transport)
class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_stall_asserts_auto_transport_hold_once_and_first_frame_releases_it():
    bus = InProcessBus()
    clock = FakeClock()
    source = NetworkInput("unused", bus=bus, session_id="sess_1", hold_after_s=1.0, clock=clock, start=False)
    source._last_media = clock()
    source._watchdog.start()
    try:
        clock.now += 0.9
        time.sleep(0.2)
        assert bus.of(Topic.HOLD) == [], "no hold before the 1.0 s threshold"
        clock.now += 0.5
        deadline = time.monotonic() + 2
        while not bus.of(Topic.HOLD) and time.monotonic() < deadline:
            time.sleep(0.02)
        time.sleep(0.2)
        holds = bus.of(Topic.HOLD)
        assert len(holds) == 1, "one assert, not one per watchdog tick"
        assert holds[0].source == HoldSource.AUTO_TRANSPORT and holds[0].active is True
        events = bus.of(Topic.SAFETY_EVENT)
        assert len(events) == 1
        assert events[0].modality == Modality.SYSTEM and events[0].category == Category.HOLD
        assert events[0].session_id == "sess_1" and events[0].detail_code == "ingest_drop"
        assert source.hold_active
        source._release_hold()
        source._release_hold()
        released = bus.of(Topic.HOLD)
        assert [h.active for h in released] == [True, False]
        assert released[1].source == HoldSource.AUTO_TRANSPORT
    finally:
        source._stop.set()
        source._watchdog.join(2)


def test_no_hold_before_the_first_media_ever_arrives():
    bus = InProcessBus()
    clock = FakeClock()
    source = NetworkInput("unused", bus=bus, hold_after_s=0.1, clock=clock, start=False)
    source._watchdog.start()
    try:
        clock.now += 50
        time.sleep(0.2)
        assert bus.of(Topic.HOLD) == []
    finally:
        source._stop.set()
        source._watchdog.join(2)


def test_file_end_triggers_the_hold_and_the_next_generation_releases_it(tmp_path, monkeypatch):
    monkeypatch.setattr(NetworkInput, "_RECONNECT_DELAYS", (1.2,))
    path = str(tmp_path / "in.nut")
    write_nut(path, n_frames=8, size=(64, 48), fps=25, audio=False)
    bus = InProcessBus()
    source = NetworkInput(path, container_options=FILE_OPTIONS, bus=bus, hold_after_s=0.3, queue_size=64)
    try:
        deadline = time.monotonic() + 8
        while len(bus.of(Topic.HOLD)) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        holds = bus.of(Topic.HOLD)
        assert [h.active for h in holds[:2]] == [True, False]
        assert all(h.source == HoldSource.AUTO_TRANSPORT for h in holds)
        assert source.generation >= 2
    finally:
        source.close()


def test_null_sink_counts_frames_and_samples_and_never_blocks():
    sink = NullFrameSink()
    image = np.zeros((720, 1280, 3), np.uint8)
    for i in range(10):
        sink.write(i / 30, image, np.zeros(1600, np.int16))
    sink.write(0.0, image, np.zeros(1600, np.int16))  # a replayed pts is counted, not fatal
    assert sink.frames == 11 and sink.audio_samples == 11 * 1600 and sink.pts_nonmonotonic == 1
    assert sink.healthy
    sink.close()
    sink.write(1.0, image, None)
    assert sink.frames == 11 and not sink.healthy
