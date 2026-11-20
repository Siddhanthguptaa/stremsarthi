"""Muxing in a killable worker process, so a network connect or write can never freeze the engine.

Why a process: PyAV opens an RTMP output with ``avio_open`` and ``avformat_write_header``, and both hold the
Python GIL for the whole connect and handshake. In the engine process that would freeze every thread (the
frame loop included) for as long as a destination takes to answer, up to the connect timeout, and it would
deadlock the in-process TLS forwarder that the connect is waiting for. The worker owns the libav connection;
the parent only sends already-encoded packets over a private socketpair and can kill the worker at any time.

``AvFlvMuxer`` was written for IRL Shield's ``relay/stream.py`` (MIT, Copyright (c) 2026 Elan Nash; see
``relay/LICENSE``): a FLV muxer templated from the encoder's header that copies packets without re-encoding.
"""

from __future__ import annotations

import io
import logging
import multiprocessing
import select
import socket
from typing import Any

from relay.encoder import (
    AUDIO_RATE,
    KIND_AUDIO,
    KIND_VIDEO,
    EncodedPacket,
    StreamInfo,
    _message,
    _receive,
    time_base,
)

log = logging.getLogger("relay.muxer")

CONNECT_TIMEOUT_S = 8.0  # libav connect and handshake limit inside the worker
SPAWN_ALLOWANCE_S = 12.0  # spawn plus imports on a loaded laptop, on top of the connect limit
WRITE_DEADLINE_S = 5.0  # a worker that does not take a packet for this long is dead or stuck


class MuxerError(RuntimeError):
    """Carries a fixed code only: libav text can contain the stream URL."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class AvFlvMuxer:
    """Muxes already-encoded packets into an FLV/RTMP connection with PyAV (no re-encode)."""

    def __init__(self, url: str, info: StreamInfo, *, timeout_s: float = CONNECT_TIMEOUT_S):
        import av

        av.logging.set_level(None)  # libav messages can contain credentialed URLs
        self._av, self._fps = av, info.fps
        template = av.open(io.BytesIO(info.header), format="flv")
        self._out: Any = None
        try:
            options = {"flvflags": "no_duration_filesize"}
            self._out = av.open(url, "w", format="flv", options=options, timeout=timeout_s)
            self._streams: dict[int, Any] = {}
            for stream in template.streams:
                copied = self._out.add_stream_from_template(stream, opaque=True)
                copied.time_base = stream.time_base
                if stream.type == "video":
                    # The header-only template has no picture size; the encoder owns the real one.
                    copied.width, copied.height = info.width, info.height
                    # The encoder emits Annex B packets. The FLV muxer converts them to length-prefixed
                    # AVC only when it is given the encoder's own (Annex B) extradata.
                    if info.video_extradata:
                        copied.codec_context.extradata = info.video_extradata
                else:
                    try:  # the FLV header only knows 44.1 kHz; the AAC config says 48 kHz
                        copied.codec_context.sample_rate = AUDIO_RATE
                    except Exception:  # noqa: BLE001 - the muxer then keeps the header value
                        pass
                self._streams[KIND_VIDEO if stream.type == "video" else KIND_AUDIO] = copied
            if set(self._streams) != {KIND_VIDEO, KIND_AUDIO}:
                raise ValueError("encoder header lacks a stream")
            self._out.start_encoding()
        except BaseException:
            self.close()
            raise
        finally:
            template.close()

    def write(self, packet: EncodedPacket) -> None:
        clone = self._av.Packet(packet.data)
        clone.pts, clone.dts, clone.duration = packet.pts, packet.dts, packet.duration
        clone.time_base = time_base(packet.kind, self._fps)
        clone.is_keyframe = packet.keyframe
        clone.stream = self._streams[packet.kind]
        self._out.mux(clone)

    def close(self) -> None:
        out, self._out = self._out, None
        if out is not None:
            try:
                out.close()
            except Exception:  # noqa: BLE001 - libav text can contain the URL; keep nothing
                pass


def _mux_worker(channel: socket.socket) -> None:
    """Worker entry: open the connection, report once, then mux packets until the parent closes the socket."""
    muxer: AvFlvMuxer | None = None
    try:
        url, info, timeout_s = _receive(channel)
        try:
            muxer = AvFlvMuxer(url, info, timeout_s=timeout_s)
        except Exception as error:  # noqa: BLE001 - class name only, never libav text
            _message(channel, ("error", type(error).__name__))
            return
        _message(channel, ("ok", None))
        while True:
            fields = _receive(channel)
            muxer.write(EncodedPacket(*fields))
    except (EOFError, OSError):
        pass  # the parent closed the socket: the normal end
    except Exception as error:  # noqa: BLE001
        try:
            _message(channel, ("error", type(error).__name__))
        except OSError:
            pass
    finally:
        if muxer is not None:
            muxer.close()
        try:
            channel.close()
        except OSError:
            pass


class ProcessMuxer:
    """Same interface as ``AvFlvMuxer`` (``write``/``close``), but the libav connection lives in a worker.

    Construction connects (raises ``MuxerError`` with a fixed code on failure or timeout). ``write`` hands the
    packet to the worker and returns; a dead or stuck worker raises on the next ``write``. ``kill`` may be
    called from any thread to unblock a stalled write.
    """

    def __init__(self, url: str, info: StreamInfo, *, timeout_s: float = CONNECT_TIMEOUT_S):
        ctx = multiprocessing.get_context("spawn")
        parent, child = socket.socketpair()
        self._channel: socket.socket | None = parent
        self._process: Any = ctx.Process(target=_mux_worker, args=(child,), name="relay-muxer", daemon=True)
        try:
            self._process.start()
        except BaseException:
            self.kill()
            raise MuxerError("muxer-spawn") from None
        finally:
            child.close()
        try:
            # The URL (it holds the stream key) travels over the private socketpair, never on a command line.
            parent.settimeout(WRITE_DEADLINE_S)
            _message(parent, (url, info, timeout_s))
            status, detail = _receive(parent, timeout=timeout_s + SPAWN_ALLOWANCE_S)
        except TimeoutError:
            self.kill()
            raise MuxerError("muxer-connect-timeout") from None
        except Exception:  # noqa: BLE001 - EOF, reset, bad message: the worker is gone
            self.kill()
            raise MuxerError("muxer-exited") from None
        if status != "ok":
            self.kill()
            raise MuxerError(f"muxer-connect-failed:{detail}")

    @property
    def pid(self) -> int | None:
        return getattr(self._process, "pid", None)

    def write(self, packet: EncodedPacket) -> None:
        channel = self._channel
        if channel is None:
            raise MuxerError("muxer-closed")
        try:
            if select.select([channel], [], [], 0)[0]:  # the worker only speaks to report an error or exit
                raise MuxerError("muxer-exited")
            channel.settimeout(WRITE_DEADLINE_S)
            _message(
                channel,
                (packet.kind, packet.data, packet.pts, packet.dts, packet.duration, packet.keyframe),
            )
        except MuxerError:
            self.kill()
            raise
        except Exception:  # noqa: BLE001
            self.kill()
            raise MuxerError("muxer-write") from None

    def kill(self) -> None:
        """Stop the worker at once (thread-safe, idempotent). A write blocked on the socket then fails."""
        process, channel = self._process, self._channel
        self._channel = None
        if channel is not None:
            try:
                channel.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                channel.close()
            except OSError:
                pass
        if process is not None:
            try:
                process.join(timeout=0.3)  # a healthy worker sees EOF and closes the FLV stream itself
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=2)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=2)
                if not process.is_alive():
                    process.close()
                    self._process = None
            except Exception:  # noqa: BLE001
                pass

    close = kill
