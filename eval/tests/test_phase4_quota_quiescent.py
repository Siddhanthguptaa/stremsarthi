"""Gate 4 YouTube quota check: governor units vs server units, read at a quiescent point (CallGate).

The governor charges a call at its start and the mock counts it on arrival, so a reading taken while a call is in
flight is off by that call's cost (the 1911 vs 1906 seen in the gate run). These tests pin the cause, the fix, and
that the check still fails for a governor that really mischarges.
"""

import asyncio

import aiohttp
import pytest

from chat.common import ManualClock, Secret
from chat.youtube import QuotaGovernor, YouTubeAuth, YouTubeClient, YouTubeSession, make_http
from chat.youtube.auth import YouTubeTokens
from eval.mock_platforms import YouTubeMock
from eval.phase4_conn import CallGate, units_at_rest

pytestmark = pytest.mark.asyncio

LIST = ("GET", "/youtube/v3/liveChat/messages", "list")
PARAMS = {"liveChatId": "LIVECHAT_SPECIMEN_1", "part": "snippet", "maxResults": 5}


class UndercountingGovernor(QuotaGovernor):
    """Seeded defect: charges 4 units for a list (the server charges 5)."""

    def try_spend(self, kind):  # type: ignore[no-untyped-def]
        ok = super().try_spend(kind)
        if ok and kind == "list":
            self.used_read -= 1
        return ok


class SkipsDeletesGovernor(QuotaGovernor):
    """Seeded defect: deletes are never charged."""

    def try_spend(self, kind):  # type: ignore[no-untyped-def]
        return True if kind == "delete" else super().try_spend(kind)


async def _rig(governor_cls):
    clock = ManualClock()
    mock = YouTubeMock(now=clock.wall)
    await mock.start()
    aio = aiohttp.ClientSession()
    http = make_http(aio, loopback_origin=mock.origin)
    auth = YouTubeAuth(mock.client_id, Secret("specimen-secret"), http, clock=clock)
    tokens = YouTubeTokens(
        Secret(mock.issue_access_token()), Secret(mock.refresh_token), clock.monotonic() + 3600
    )
    gov = governor_cls(clock=clock)
    client = YouTubeClient(http, YouTubeSession(auth, tokens, clock=clock), gov)
    return mock, aio, client, gov, CallGate(client)


async def _close(mock, aio):
    await aio.close()
    await mock.stop()


async def test_honest_governor_matches_at_rest_even_with_a_poller_hammering():
    mock, aio, client, gov, gate = await _rig(QuotaGovernor)
    try:
        for i in range(3):
            mock.add_message(f"m{i}")
        stop = False

        async def hammer() -> None:
            while not stop:
                await client.call(*LIST, params=PARAMS)
                await asyncio.sleep(0)

        task = asyncio.create_task(hammer())
        try:
            for _ in range(2000):  # until the first call has landed
                if mock.units_used:
                    break
                await asyncio.sleep(0.001)
            for _ in range(25):
                g, s = await units_at_rest(gate, gov, mock)
                assert g == s and s > 0
                await asyncio.sleep(0)
        finally:
            stop = True
            await task
    finally:
        await _close(mock, aio)


async def test_the_race_is_real_and_the_gate_closes_it():
    """A list that the governor has charged but that has not reached the server yet: an unguarded read is 5 units
    apart. The gated read waits for the call to land, then the counters are equal."""
    mock, aio, client, gov, gate = await _rig(QuotaGovernor)
    try:
        release = asyncio.Event()
        real_request = client.http.request

        async def held_request(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            await release.wait()  # the governor has already charged; the request is not on the wire yet
            return await real_request(*args, **kwargs)

        client.http.request = held_request
        task = asyncio.create_task(client.call(*LIST, params=PARAMS))
        for _ in range(200):
            if gate.inflight and gov.used_total:
                break
            await asyncio.sleep(0.005)
        assert (gov.used_total, mock.units_used) == (5, 0), "the unguarded read is off by the in-flight call"
        reader = asyncio.create_task(units_at_rest(gate, gov, mock))
        await asyncio.sleep(0.05)
        assert not reader.done(), "the reader must wait while a call is in flight"
        release.set()
        await task
        assert await asyncio.wait_for(reader, 5) == (5, 5)
    finally:
        await _close(mock, aio)


@pytest.mark.parametrize("governor_cls", [UndercountingGovernor, SkipsDeletesGovernor])
async def test_seeded_mischarge_still_fails_the_check(governor_cls):
    mock, aio, client, gov, gate = await _rig(governor_cls)
    try:
        ids = [mock.add_message(f"m{i}") for i in range(4)]
        for _ in range(3):
            await client.call(*LIST, params=PARAMS)
        for mid in ids[:2]:
            await client.call("DELETE", "/youtube/v3/liveChat/messages", "delete", params={"id": mid})
        g, s = await units_at_rest(gate, gov, mock)
        assert g != s, "a governor that mischarges must not pass the equality check"
    finally:
        await _close(mock, aio)


async def test_gate_reopens_after_the_read_and_survives_an_exception():
    mock, aio, client, gov, gate = await _rig(QuotaGovernor)
    try:
        with pytest.raises(RuntimeError):
            async with gate.quiescent():
                raise RuntimeError("boom")
        await asyncio.wait_for(client.call(*LIST, params=PARAMS), 5)  # not left closed
        assert gate.inflight == 0
        g, s = await units_at_rest(gate, gov, mock)
        assert g == s == 5
    finally:
        await _close(mock, aio)
