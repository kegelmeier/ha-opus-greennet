"""Phase R4: event stream lifecycle of the REST coordinator.

Findings B-2 (no pause after a short-lived stream, fixed pauses without
jitter) and the connection generation of section 6 of
``Implementations-Plan-Rest-API.md``. The coordinator runs with a mocked
Home Assistant and a scripted client, so every pause and every attempt is
observed exactly; ``asyncio.sleep`` is replaced and never waits.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.opus_greennet import coordinator
from custom_components.opus_greennet.api import (
    OpusAuthError,
    OpusConnectionError,
    OpusTimeoutError,
)
from custom_components.opus_greennet.coordinator import (
    STREAM_BACKOFF_INITIAL,
    STREAM_BACKOFF_JITTER,
    STREAM_BACKOFF_MAX,
    OpusGreenNetCoordinator,
    stream_retry_delay,
)

MODULE = "custom_components.opus_greennet.coordinator"
DEVICES = {"header": {"content": "devices"}, "devices": []}
NOMINAL = [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]


@pytest.fixture
def coord() -> Iterator[OpusGreenNetCoordinator]:
    """A coordinator whose availability changes are not dispatched."""
    coordinator = OpusGreenNetCoordinator(MagicMock(), MagicMock(), "AABB0011")
    coordinator._gateway_available = True  # noqa: SLF001
    coordinator.client.async_get = AsyncMock()
    with (
        patch(f"{MODULE}.async_dispatcher_send"),
        patch(f"{MODULE}.random.random", return_value=0.0),
    ):
        yield coordinator


class ScriptedStreams:
    """``client.async_stream`` that plays one scripted outcome per attempt.

    An outcome is a list of documents (the stream ends after them) or an
    exception (raised before any document). The last outcome repeats.
    """

    def __init__(self, *outcomes: list[dict[str, object]] | Exception) -> None:
        self.outcomes = list(outcomes)
        self.attempts = 0
        self.on_document: Callable[[int], None] | None = None

    def __call__(self) -> AsyncIterator[dict[str, object]]:
        outcome = self.outcomes[min(self.attempts, len(self.outcomes) - 1)]
        self.attempts += 1
        return self._play(outcome)

    async def _play(
        self, outcome: list[dict[str, object]] | Exception
    ) -> AsyncIterator[dict[str, object]]:
        if isinstance(outcome, Exception):
            raise outcome
        for index, document in enumerate(outcome):
            if self.on_document is not None:
                self.on_document(index)
            # Documents parsed from one chunk follow each other without any
            # suspension point, as in ``OpusGatewayClient.async_stream``.
            yield document


def _record_pauses(
    coord: OpusGreenNetCoordinator, limit: int
) -> tuple[list[float], Callable[[float], object]]:
    """Return the recorded pauses and a ``sleep`` that unloads after ``limit``."""
    pauses: list[float] = []

    async def sleep(delay: float) -> None:
        pauses.append(delay)
        if len(pauses) >= limit:
            coord._unloaded = True  # noqa: SLF001

    return pauses, sleep


# ── Backoff schedule ───────────────────────────────────────────────────────


def test_backoff_doubles_up_to_the_bound_without_jitter() -> None:
    assert [stream_retry_delay(n, 0.0) for n in range(8)] == NOMINAL
    assert (STREAM_BACKOFF_INITIAL, STREAM_BACKOFF_MAX) == (1.0, 60.0)


def test_jitter_only_lengthens_the_pause_and_stays_bounded() -> None:
    for failures in range(12):
        nominal = stream_retry_delay(failures, 0.0)
        longest = stream_retry_delay(failures, 1.0)
        assert longest == pytest.approx(nominal * (1 + STREAM_BACKOFF_JITTER))
        assert nominal <= stream_retry_delay(failures, 0.5) <= longest
    assert stream_retry_delay(10_000, 1.0) == pytest.approx(
        STREAM_BACKOFF_MAX * (1 + STREAM_BACKOFF_JITTER)
    )
    # Out-of-range input never shortens the pause or exceeds the bound.
    assert stream_retry_delay(-3, -1.0) == STREAM_BACKOFF_INITIAL
    assert stream_retry_delay(100, 7.0) == pytest.approx(75.0)


def test_jitter_is_random_by_default() -> None:
    with patch(f"{MODULE}.random.random", side_effect=[0.2, 0.8]):
        first = stream_retry_delay(3)
        second = stream_retry_delay(3)
    assert (first, second) == (pytest.approx(8.4), pytest.approx(9.6))


def test_steady_state_is_never_more_frequent_than_before_r4() -> None:
    """Old fixed pauses: 1, 2, 5, 10, 30, 60 s (protocol contract 12.4)."""
    old = [1.0, 2.0, 5.0, 10.0, 30.0, 60.0]
    new_shortest = [stream_retry_delay(n, 0.0) for n in range(6)]
    # The first attempts are never sooner than before ...
    assert new_shortest[:2] == old[:2]
    # ... at most six attempts fit into the first minute, as before ...
    assert sum(old[:5]) <= 60 < sum(old[:6])
    assert sum(new_shortest[:6]) > 60
    # ... and a long outage is retried at most once per 60 s, as before.
    assert stream_retry_delay(50, 0.0) >= old[-1]


# ── Short-lived and failing streams (B-2) ─────────────────────────────────


async def test_stream_closed_after_snapshot_backs_off_instead_of_looping(
    coord,
) -> None:
    """Gateway closes every stream right after the device list."""
    streams = ScriptedStreams([DEVICES])
    coord.client.async_stream = streams
    pauses, sleep = _record_pauses(coord, limit=len(NOMINAL))
    with patch(f"{MODULE}.asyncio.sleep", sleep):
        await coord._async_run_stream(coord._stream_generation)  # noqa: SLF001
    # Before R4: zero pause and an unbounded number of device list downloads.
    assert pauses == NOMINAL
    assert streams.attempts == len(NOMINAL)
    # The gateway answers, so entities stay available throughout (the test
    # ends the loop by unloading, hence the private flag).
    assert coord._gateway_available  # noqa: SLF001
    assert coord._stream_connected is False  # noqa: SLF001


async def test_healthy_stream_resets_the_backoff_but_still_pauses(coord) -> None:
    streams = ScriptedStreams([DEVICES])
    coord.client.async_stream = streams
    pauses, sleep = _record_pauses(coord, limit=4)
    with (
        patch(f"{MODULE}.asyncio.sleep", sleep),
        patch(f"{MODULE}.STREAM_MIN_HEALTHY_SECONDS", 0.0),
    ):
        await coord._async_run_stream(coord._stream_generation)  # noqa: SLF001
    assert pauses == [STREAM_BACKOFF_INITIAL] * 4
    assert coord._gateway_available  # noqa: SLF001


async def test_stream_counts_as_healthy_after_the_minimum_duration(coord) -> None:
    streams = ScriptedStreams([DEVICES])
    coord.client.async_stream = streams
    pauses, sleep = _record_pauses(coord, limit=3)
    clock = iter([0.0, 9.9, 100.0, 110.0, 200.0, 200.5])
    with (
        patch(f"{MODULE}.asyncio.sleep", sleep),
        patch(f"{MODULE}.monotonic", lambda: next(clock)),
        patch.object(coord, "_handle_document"),
    ):
        await coord._async_run_stream(coord._stream_generation)  # noqa: SLF001
    # 9.9 s: short-lived (failure 0 -> 1 s); 10 s: healthy, reset (1 s);
    # 0.5 s: short-lived again after the reset (2 s).
    assert pauses == [1.0, 1.0, 2.0]


async def test_failed_connection_marks_unavailable_and_backs_off(coord) -> None:
    streams = ScriptedStreams(OpusConnectionError("refused"))
    coord.client.async_stream = streams
    pauses, sleep = _record_pauses(coord, limit=5)
    with patch(f"{MODULE}.asyncio.sleep", sleep):
        await coord._async_run_stream(coord._stream_generation)  # noqa: SLF001
    assert pauses == NOMINAL[:5]
    assert not coord._gateway_available  # noqa: SLF001


async def test_recovery_after_failures_continues_the_schedule(coord) -> None:
    """A short-lived success does not reset the failures before it."""
    streams = ScriptedStreams(
        OpusTimeoutError("silent"), OpusConnectionError("refused"), [DEVICES]
    )
    coord.client.async_stream = streams
    pauses, sleep = _record_pauses(coord, limit=4)
    with patch(f"{MODULE}.asyncio.sleep", sleep):
        await coord._async_run_stream(coord._stream_generation)  # noqa: SLF001
    assert pauses == [1.0, 2.0, 4.0, 8.0]
    # The stream that delivered the device list made the gateway available.
    assert coord._gateway_available  # noqa: SLF001


async def test_unexpected_error_also_backs_off(coord) -> None:
    streams = ScriptedStreams([DEVICES])
    coord.client.async_stream = streams
    pauses, sleep = _record_pauses(coord, limit=3)
    with (
        patch(f"{MODULE}.asyncio.sleep", sleep),
        patch.object(coord, "_handle_document", side_effect=RuntimeError("boom")),
    ):
        await coord._async_run_stream(coord._stream_generation)  # noqa: SLF001
    assert pauses == [1.0, 2.0, 4.0]


async def test_auth_failure_ends_the_reader_without_another_attempt(coord) -> None:
    streams = ScriptedStreams(OpusAuthError("status 401"))
    coord.client.async_stream = streams
    on_auth_failed = MagicMock()
    coord._on_auth_failed = on_auth_failed  # noqa: SLF001
    sleep = AsyncMock()
    with patch(f"{MODULE}.asyncio.sleep", sleep):
        await coord._async_run_stream(coord._stream_generation)  # noqa: SLF001
    assert streams.attempts == 1
    sleep.assert_not_called()
    on_auth_failed.assert_called_once()
    assert not coord.available


# ── Connection generation ─────────────────────────────────────────────────


def _start_without_running(coord: OpusGreenNetCoordinator) -> list[str]:
    """Let ``_start_stream`` create its task without running the coroutine."""
    created: list[str] = []

    def create_task(coroutine, name):
        coroutine.close()
        created.append(name)
        task = MagicMock()
        task.done.return_value = False
        return task

    coord._create_task = create_task  # noqa: SLF001
    return created


async def test_replaced_reader_drops_documents_it_already_parsed(coord) -> None:
    """Restart during a chunk with two documents: the second one is stale."""
    created = _start_without_running(coord)
    coord._start_stream()  # noqa: SLF001
    generation = coord._stream_generation  # noqa: SLF001
    first = {"header": {"content": "telegram"}, "telegram": {"deviceId": "A"}}
    second = {"header": {"content": "telegram"}, "telegram": {"deviceId": "B"}}
    streams = ScriptedStreams([first, second])
    coord.client.async_stream = streams
    handled: list[object] = []

    def handle(document: dict[str, object]) -> None:
        handled.append(document)
        # e.g. the health probe saw a gateway restart meanwhile
        coord._restart_stream()  # noqa: SLF001

    sleep = AsyncMock()
    with (
        patch.object(coord, "_handle_document", side_effect=handle),
        patch(f"{MODULE}.asyncio.sleep", sleep),
    ):
        await coord._async_run_stream(generation)  # noqa: SLF001
    assert handled == [first]
    assert created == ["stream", "stream"]
    assert coord._stream_generation == generation + 1  # noqa: SLF001
    # The replaced reader neither reconnects nor marks the stream open.
    assert streams.attempts == 1
    sleep.assert_not_called()
    assert coord._stream_connected is False  # noqa: SLF001


async def test_replaced_reader_does_not_change_availability(coord) -> None:
    _start_without_running(coord)
    coord._start_stream()  # noqa: SLF001
    old_generation = coord._stream_generation  # noqa: SLF001
    coord._restart_stream()  # noqa: SLF001
    coord.client.async_stream = ScriptedStreams(OpusConnectionError("reset"))
    sleep = AsyncMock()
    with patch(f"{MODULE}.asyncio.sleep", sleep):
        await coord._async_run_stream(old_generation)  # noqa: SLF001
    assert coord._gateway_available  # noqa: SLF001
    sleep.assert_not_called()


async def test_replaced_reader_auth_failure_is_left_to_the_new_reader(coord) -> None:
    _start_without_running(coord)
    coord._start_stream()  # noqa: SLF001
    old_generation = coord._stream_generation  # noqa: SLF001
    coord._restart_stream()  # noqa: SLF001
    coord.client.async_stream = ScriptedStreams(OpusAuthError("status 401"))
    on_auth_failed = MagicMock()
    coord._on_auth_failed = on_auth_failed  # noqa: SLF001
    await coord._async_run_stream(old_generation)  # noqa: SLF001
    on_auth_failed.assert_not_called()
    assert coord._gateway_available  # noqa: SLF001


async def test_every_start_gets_a_new_generation(coord) -> None:
    created = _start_without_running(coord)
    generations = []
    for _ in range(3):
        coord._restart_stream()  # noqa: SLF001
        generations.append(coord._stream_generation)  # noqa: SLF001
    assert generations == sorted(set(generations))
    assert created == ["stream"] * 3


async def test_cancelled_reader_stops_during_its_pause(coord) -> None:
    """Unload cancels a reader that waits for its next attempt."""
    coord.client.async_stream = ScriptedStreams(OpusConnectionError("refused"))
    with patch(f"{MODULE}.STREAM_BACKOFF_INITIAL", 30.0):
        task = asyncio.ensure_future(
            coord._async_run_stream(coord._stream_generation)  # noqa: SLF001
        )
        for _ in range(5):
            await asyncio.sleep(0)
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


def test_module_has_no_fixed_retry_table() -> None:
    """The fixed pauses of R2 are gone; only the bounded backoff remains."""
    assert not hasattr(coordinator, "STREAM_RETRY_DELAYS")


async def test_replaced_reader_closes_its_stream(coord) -> None:
    """Leaving the loop early closes the generator, releasing the response."""
    _start_without_running(coord)
    coord._start_stream()  # noqa: SLF001
    generation = coord._stream_generation  # noqa: SLF001
    closed: list[bool] = []

    async def stream() -> AsyncIterator[dict[str, object]]:
        try:
            yield DEVICES
            yield DEVICES
        finally:
            closed.append(True)

    coord.client.async_stream = stream
    with patch.object(
        coord, "_handle_document", side_effect=lambda _: coord._restart_stream()
    ):
        await coord._async_run_stream(generation)  # noqa: SLF001
    assert closed == [True]
