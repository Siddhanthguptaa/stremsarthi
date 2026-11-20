"""Fixtures for the relay tests. Integration tests need the pinned MediaMTX binary and ffmpeg and are
skipped (with the reason) when either is missing. They use free ports, never the default ones."""

from __future__ import annotations

import pytest

from relay.mediamtx import BinaryVerificationError, MediaMTXSupervisor, verified_binary
from relay.tests.support_media import free_address, have_ffmpeg


@pytest.fixture(scope="module")
def mtx():
    if not have_ffmpeg():
        pytest.skip("ffmpeg not on PATH")
    try:
        binary = verified_binary()
    except BinaryVerificationError as error:
        pytest.skip(f"MediaMTX binary unavailable: {error}")
    supervisor = MediaMTXSupervisor(free_address(), binary=binary, srt_latency_ms=120)
    supervisor.start()
    try:
        yield supervisor
    finally:
        supervisor.stop()
