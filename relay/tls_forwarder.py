"""Bounded loopback RTMP to verified TLS. It never sees an RTMP key or URL, only bytes.

Adapted from IRL Shield ``relay/tls_forwarder.py`` (MIT, Copyright (c) 2026 Elan Nash; see
``relay/LICENSE``). Changes: type hints and ruff formatting; ``cafile`` is documented as the only
way to change trust (tests point it at a private CA); ``reset_connections()`` lets the egress
watchdog cut a stalled destination without touching the others; the stats carry fixed error codes
only. One instance serves one destination (``relay/destinations.py`` builds one per destination).
"""

from __future__ import annotations

import select
import socket
import ssl
import threading
import time
from typing import Any


class TLSForwarder:
    """One local listener survives writer reconnects; each TLS peer is verified.

    Production uses the system CAs and pins the peer hostname (certificate and SNI). A private CA file
    supports explicitly configured trust, including the synthetic loopback certificate used by the
    tests. An RTMP session EOF closes both sockets; buffered media is never replayed on a new
    connection.
    """

    capacity = 2

    def __init__(self, host: str = "live.twitch.tv", port: int = 443, *, cafile: str | None = None) -> None:
        if not isinstance(host, str) or not host or any(c in host for c in "/@:?\r\n\x00 "):
            raise ValueError("Invalid TLS peer hostname")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Invalid TLS peer port")
        self.host, self.peer_port = host, port
        self.context = ssl.create_default_context(cafile=cafile)
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._sockets: set[Any] = set()
        self._workers: set[threading.Thread] = set()
        self.listener: socket.socket | None = None
        self.thread: threading.Thread | None = None
        self.port: int | None = None
        self._stats: dict[str, Any] = {"connections": 0, "failures": 0, "rejected": 0, "last_error": None}

    @property
    def stats(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._stats)

    def __enter__(self) -> TLSForwarder:
        return self.start()

    def start(self) -> TLSForwarder:
        if self.listener is not None or self._stopped.is_set():
            raise RuntimeError("TLS forwarder cannot be restarted")
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.listener.bind(("127.0.0.1", 0))
            self.listener.listen(self.capacity)
            self.listener.settimeout(0.2)
            self.port = self.listener.getsockname()[1]
            self.thread = threading.Thread(target=self._accept, name="verified-tls-listener", daemon=True)
            self.thread.start()
        except BaseException:
            self.listener.close()
            raise
        return self

    def _accept(self) -> None:
        assert self.listener is not None
        while not self._stopped.is_set():
            try:
                client, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with self._lock:
                if self._stopped.is_set() or len(self._workers) >= self.capacity:
                    client.close()
                    self._stats["rejected"] += 1
                    continue
                self._sockets.add(client)
                self._stats["connections"] += 1
                worker = threading.Thread(
                    target=self._serve, args=(client,), name="verified-tls-connection", daemon=True
                )
                self._workers.add(worker)
                worker.start()

    def _serve(self, client: socket.socket) -> None:
        peer: Any = None
        raw: socket.socket | None = None
        try:
            client.settimeout(5)
            raw = socket.create_connection((self.host, self.peer_port), timeout=5)
            with self._lock:
                if self._stopped.is_set():
                    return
                self._sockets.add(raw)
            # Register before the handshake so shutdown can interrupt an unfinished TLS exchange.
            # No RTMP byte is forwarded before verification.
            peer = self.context.wrap_socket(raw, server_hostname=self.host, do_handshake_on_connect=False)
            with self._lock:
                self._sockets.discard(raw)
                self._sockets.add(peer)
                stopped = self._stopped.is_set()
            if stopped:
                return
            peer.do_handshake()
            self._pipe(client, peer)
        except ssl.SSLCertVerificationError:
            self._failed("tls-verification-failed")
        except (OSError, ssl.SSLError):
            if not self._stopped.is_set():
                self._failed("tls-forwarding-failed")
        finally:
            for connection in (client, peer, raw):
                if connection is not None:
                    with self._lock:
                        self._sockets.discard(connection)
                    try:
                        connection.close()
                    except OSError:
                        pass
            with self._lock:
                self._workers.discard(threading.current_thread())

    def _failed(self, code: str) -> None:
        with self._lock:
            self._stats["failures"] += 1
            self._stats["last_error"] = code

    def _pipe(self, client: socket.socket, peer: Any) -> None:
        while not self._stopped.is_set():
            readable, _, _ = select.select([client, peer], [], [], 0 if peer.pending() else 0.2)
            if peer.pending() and peer not in readable:
                readable.append(peer)
            for source in readable:
                data = source.recv(65536)
                if not data:
                    return
                (peer if source is client else client).sendall(data)

    def reset_connections(self) -> int:
        """Cut every live connection (the listener stays). Used by the watchdog on a stalled write."""
        with self._lock:
            sockets = list(self._sockets)
        for connection in sockets:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        return len(sockets)

    def close(self) -> None:
        self._stopped.set()
        if self.listener is not None:
            self.listener.close()
        with self._lock:
            sockets = list(self._sockets)
            workers = list(self._workers)
        for connection in sockets:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass
        threads = ([self.thread] if self.thread else []) + workers
        deadline = time.monotonic() + 6
        for worker in threads:
            worker.join(max(0.0, deadline - time.monotonic()))
        if any(worker.is_alive() for worker in threads):
            raise RuntimeError("TLS forwarder did not stop")

    def __exit__(self, *error: object) -> None:
        self.close()
