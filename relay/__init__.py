"""Stream Sarthi relay: ingest (MediaMTX + PyAV reader) and, from T1.2, the encoder and egress.

Parts of this package are adapted from IRL Shield (MIT), see ``relay/LICENSE`` and
``docs/THIRD_PARTY.md``. The relay moves bytes in and out of the engine; it never decides what
is private. Nothing here writes encoded or decoded media to disk.
"""
