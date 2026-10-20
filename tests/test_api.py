"""Tests for the REST client, its error mapping and the stream splitter."""

from __future__ import annotations

import asyncio
import json

import aiohttp
import pytest

from custom_components.opus_greennet.api import (
    MAX_DOCUMENT_SIZE,
    PRIORITY_BACKGROUND,
    PRIORITY_COMMAND,
    PRIORITY_QUERY,
    JsonDocumentSplitter,
    OpusAuthError,
    OpusConnectionError,
    OpusGatewayClient,
    OpusResponseError,
    OpusTimeoutError,
    PriorityLock,
    decode_response,
    gateway_uptime_value,
    response_status,
)
from tests.fake_gateway import FakeGateway, device_object


@pytest.fixture
async def session():
    async with aiohttp.ClientSession() as client_session:
        yield client_session


def _client(session: aiohttp.ClientSession, gateway: FakeGateway, **kw):
    config = {**gateway.config, **kw}
    return OpusGatewayClient(
        session,
        config["host"],
        config["port"],
        config["username"],
        config["password"],
    )


# ── response validation ────────────────────────────────────────────────────


@pytest.mark.parametrize("status", [200, 201, 200.0, "200"])
def test_decode_response_accepts_success(status: object) -> None:
    assert (
        decode_response(json.dumps({"header": {"httpStatus": status}, "x": 1}))["x"]
        == 1
    )


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("not json", "invalid JSON"),
        ("[]", "invalid response object"),
        ("{}", "invalid response object"),
        ('{"x": 1}', "missing its status"),
        ('{"header": {"httpStatus": true}}', "invalid status"),
        ('{"header": {"httpStatus": 200.5}}', "invalid status"),
        ('{"header": {"httpStatus": "abc"}}', "invalid status"),
        ('{"header": {"httpStatus": []}}', "invalid status"),
        ('{"header": {"httpStatus": 400}}', "status 400"),
        (
            '{"header": {"httpStatus": 400, "message": "no matching group found"}}',
            "status 400: no matching group found",
        ),
    ],
)
def test_decode_response_rejects(text: str, reason: str) -> None:
    with pytest.raises(OpusResponseError, match=reason):
        decode_response(text)


def test_decode_response_without_required_status() -> None:
    assert decode_response('{"x": 1}', require_status=False) == {"x": 1}


def test_response_status_overflow() -> None:
    with pytest.raises(ValueError, match="invalid status"):
        response_status({"header": {"httpStatus": "1e999"}})


@pytest.mark.parametrize("value", [3600, 3600.5, "3600"])
def test_uptime_value(value: object) -> None:
    assert gateway_uptime_value({"systemUptimeResponse": {"uptime": value}}) == str(
        value
    )


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"systemUptimeResponse": []},
        {"systemUptimeResponse": {"uptime": True}},
        {"systemUptimeResponse": {"uptime": "soon"}},
        {"systemUptimeResponse": {"uptime": -1}},
        {"systemUptimeResponse": {"uptime": "nan"}},
        {"systemUptimeResponse": {"uptime": "1e999"}},
    ],
)
def test_uptime_value_rejects(data: dict) -> None:
    with pytest.raises(OpusResponseError, match="invalid uptime"):
        gateway_uptime_value(data)


# ── stream splitter ────────────────────────────────────────────────────────


def test_splitter_handles_split_strings_nesting_and_utf8() -> None:
    documents = [
        {"header": {"content": "devices"}, "devices": [{"deviceId": "A"}]},
        {"text": 'brace } bracket ] quote \\" é', "list": [1, {"a": "]"}]},
        [1, 2, 3],
    ]
    raw = "\n".join(json.dumps(doc, indent=2, ensure_ascii=False) for doc in documents)
    data = raw.encode()
    for size in (1, 2, 3, 7, 64, len(data)):
        splitter = JsonDocumentSplitter()
        found: list[str] = []
        for start in range(0, len(data), size):
            found.extend(splitter.feed(data[start : start + size]))
        assert [json.loads(text) for text in found] == documents


def test_splitter_rejects_garbage_between_documents() -> None:
    with pytest.raises(OpusResponseError, match="invalid data"):
        JsonDocumentSplitter().feed(b'{"a": 1} x')


def test_splitter_limits_document_size() -> None:
    splitter = JsonDocumentSplitter(max_size=10)
    with pytest.raises(OpusResponseError, match="too large"):
        splitter.feed(b'{"a": "0123456789"')


def test_splitter_limits_a_complete_document_within_one_chunk() -> None:
    """Phase R4, finding B-3: not only remainders growing across chunks."""
    splitter = JsonDocumentSplitter(max_size=10)
    with pytest.raises(OpusResponseError, match="too large"):
        splitter.feed(b'{"a": "0123456789"}')


def test_splitter_limits_a_document_completed_in_a_later_chunk() -> None:
    splitter = JsonDocumentSplitter(max_size=12)
    assert splitter.feed(b'{"a": "01') == []
    with pytest.raises(OpusResponseError, match="too large"):
        splitter.feed(b'23456789"}')


def test_splitter_accepts_documents_up_to_the_limit() -> None:
    document = b'{"a": "0123"}'
    splitter = JsonDocumentSplitter(max_size=len(document))
    assert splitter.feed(document + b" " + document) == [document.decode()] * 2
    # The size of one document never adds up across documents.
    assert splitter.feed(document) == [document.decode()]


def test_splitter_default_limit_is_unchanged() -> None:
    assert MAX_DOCUMENT_SIZE == 8 * 1024 * 1024


# ── priority lock ──────────────────────────────────────────────────────────


async def test_priority_lock_orders_waiters_by_priority_then_fifo() -> None:
    lock = PriorityLock()
    order: list[str] = []
    await lock.acquire()

    async def waiter(name: str, priority: int) -> None:
        await lock.acquire(priority)
        order.append(name)
        lock.release()

    tasks = [
        asyncio.create_task(waiter("background", PRIORITY_BACKGROUND)),
        asyncio.create_task(waiter("query", PRIORITY_QUERY)),
        asyncio.create_task(waiter("command-1", PRIORITY_COMMAND)),
        asyncio.create_task(waiter("command-2", PRIORITY_COMMAND)),
    ]
    await asyncio.sleep(0)
    assert lock.queue_depth == 4
    lock.release()
    await asyncio.gather(*tasks)
    assert order == ["command-1", "command-2", "query", "background"]
    assert not lock.locked()


async def test_priority_lock_cancelled_waiter_leaves_queue() -> None:
    lock = PriorityLock()
    await lock.acquire()
    task = asyncio.create_task(lock.acquire())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert lock.queue_depth == 0
    lock.release()
    assert not lock.locked()


async def test_priority_lock_cancel_after_handover_releases() -> None:
    lock = PriorityLock()
    await lock.acquire()
    task = asyncio.create_task(lock.acquire())
    await asyncio.sleep(0)
    lock.release()  # hands ownership to the waiter
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not lock.locked()


# ── HTTP ───────────────────────────────────────────────────────────────────


async def test_get_and_put_use_basic_auth(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    gateway.devices = [device_object("DEV1", "D2-01-01")]
    client = _client(session, gateway)
    info = await client.async_get("/system/info")
    assert info["systemInfo"]["eurid"] == gateway.eurid
    ack = await client.async_put(
        client.device_path("DEV1", "state"),
        {"state": {"functions": [{"key": "switch", "value": "on"}]}},
    )
    assert ack["header"]["httpStatus"] == 200
    assert gateway.requests[-1] == (
        "PUT",
        "/devices/DEV1/state",
        {"state": {"functions": [{"key": "switch", "value": "on"}]}},
    )


def test_device_path_quotes_identifiers() -> None:
    assert OpusGatewayClient.device_path("A/B", "state") == "/devices/A%2FB/state"


def test_ipv6_host_is_bracketed() -> None:
    client = OpusGatewayClient(None, "fe80::1", 8080, "admin", "pw")  # type: ignore[arg-type]
    assert client._base_url == "http://[fe80::1]:8080"  # noqa: SLF001


async def test_wrong_credentials_raise_auth_error(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    client = _client(session, gateway, password="wrong")
    with pytest.raises(OpusAuthError):
        await client.async_get("/system/info")
    with pytest.raises(OpusAuthError):
        async for _ in client.async_stream():
            pass  # pragma: no cover


async def test_error_status_raises_response_error_with_gateway_message(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    gateway.status["/devices/DEV1/state"] = 500
    client = _client(session, gateway)
    with pytest.raises(OpusResponseError, match="status 500: forced failure") as err:
        await client.async_put("/devices/DEV1/state", {"state": {}})
    assert err.value.status == 500


async def test_error_status_without_body(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    gateway.responses["/system/info"] = ""
    gateway.status.clear()
    client = _client(session, gateway)
    # An empty 200 body is invalid JSON.
    with pytest.raises(OpusResponseError, match="invalid JSON"):
        await client.async_get("/system/info")


async def test_unknown_path_reports_status(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    client = _client(session, gateway)
    with pytest.raises(OpusResponseError, match="status 404"):
        await client.async_get("/nothing")


async def test_empty_error_response_reports_http_status(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    client = _client(session, gateway)
    gateway.stream_preamble = []
    gateway.status["/devices/stream"] = 503
    with pytest.raises(OpusResponseError, match="status 503"):
        async for _ in client.async_stream():
            pass  # pragma: no cover


async def test_timeout_raises_timeout_error(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    gateway.delays["/system/uptime"] = 1
    client = _client(session, gateway)
    with pytest.raises(OpusTimeoutError):
        await client.async_get("/system/uptime", timeout=0.05)


async def test_connection_refused_raises_connection_error(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    client = _client(session, gateway)
    await gateway.stop()
    with pytest.raises(OpusConnectionError) as err:
        await client.async_get("/system/info")
    assert not isinstance(err.value, OpusTimeoutError)
    with pytest.raises(OpusConnectionError):
        async for _ in client.async_stream():
            pass  # pragma: no cover


async def test_stream_yields_device_list_then_events(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    gateway.devices = [device_object("DEV1", "D2-01-01")]
    client = _client(session, gateway)
    received: list[dict] = []

    async def consume() -> None:
        async for document in client.async_stream():
            received.append(document)

    task = asyncio.create_task(consume())
    await gateway.wait_for_stream()
    # Non-object documents are skipped.
    await gateway.push("[1, 2]")
    await gateway.push({"header": {"content": "telegram"}, "telegram": {}})
    await gateway.close_streams()
    await asyncio.wait_for(task, 5)
    assert [doc["header"]["content"] for doc in received] == ["devices", "telegram"]


async def test_stream_invalid_json_raises_response_error(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    client = _client(session, gateway)
    gateway.stream_preamble = ['{"a": tru}']
    with pytest.raises(OpusResponseError, match="invalid JSON"):
        async for _ in client.async_stream():
            pass  # pragma: no cover


async def test_stream_read_timeout_raises_timeout_error(
    session: aiohttp.ClientSession,
    gateway: FakeGateway,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("custom_components.opus_greennet.api.STREAM_IDLE_TIMEOUT", 0.05)
    client = _client(session, gateway)
    with pytest.raises(OpusTimeoutError, match="silent"):
        async for _ in client.async_stream():
            pass
