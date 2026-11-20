"""The muxer worker process: failures come back as fixed codes, a kill is immediate and idempotent."""

from __future__ import annotations

import socket
import time

import pytest

from relay.encoder import KIND_VIDEO, EncodedPacket, StreamInfo
from relay.muxer import MuxerError, ProcessMuxer

BAD_INFO = StreamInfo(header=b"not an flv header", width=1280, height=720, fps=30)


def test_a_bad_header_is_reported_by_the_worker_with_a_class_name_only():
    started = time.monotonic()
    with pytest.raises(MuxerError) as caught:
        ProcessMuxer("rtmp://127.0.0.1:9/live2/abcd-efgh-ijkl", BAD_INFO, timeout_s=3.0)
    assert caught.value.code.startswith("muxer-connect-failed:")
    assert "abcd-efgh" not in str(caught.value) and "127.0.0.1" not in str(caught.value)
    assert time.monotonic() - started < 20


def test_a_refused_connection_fails_within_the_connect_limit():
    from relay.tests.support_media import free_port

    port = free_port()  # nothing listens here

    info = _real_info()
    started = time.monotonic()
    with pytest.raises(MuxerError):
        ProcessMuxer(f"rtmp://127.0.0.1:{port}/live2/abcd-efgh-ijkl", info, timeout_s=3.0)
    assert time.monotonic() - started < 20


def test_kill_is_idempotent_and_a_write_after_it_raises():
    muxer = ProcessMuxer.__new__(ProcessMuxer)
    left, right = socket.socketpair()
    muxer._channel, muxer._process = left, None
    muxer.kill()
    muxer.kill()
    muxer.close()
    right.close()
    packet = EncodedPacket(KIND_VIDEO, b"\x00", 0, 0, 1, True)
    with pytest.raises(MuxerError, match="muxer-closed"):
        muxer.write(packet)


def _real_info() -> StreamInfo:
    """A genuine FLV header from the real encoder worker's format (h264 plus aac), without encoding a frame."""
    import io

    import av

    buffer = io.BytesIO()
    with av.open(buffer, "w", format="flv") as out:
        video = out.add_stream("libx264", rate=30)
        video.width, video.height, video.pix_fmt = 1280, 720, "yuv420p"
        audio = out.add_stream("aac", rate=48000)
        audio.layout = "stereo"
    return StreamInfo(header=buffer.getvalue(), width=1280, height=720, fps=30)
