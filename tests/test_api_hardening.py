"""REST client hardening (ported from the fubu2k fork, REST plan phase R3).

Covers finding B-1 (HTTP status and JSON status evaluated separately), the
library boundary of ``api.py``/``wire.py`` (B-6, E5), the injected web session
(Home Assistant rule ``inject-websession``), error texts without credentials
and the absence of client-side retries.

These tests need no Home Assistant instance: they exercise ``api.py`` against
the in-process ``FakeGateway`` over real HTTP and inspect the source code.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import sys
from pathlib import Path

import aiohttp
import pytest

from custom_components.opus_greennet import api
from custom_components.opus_greennet.api import (
    OpusApiError,
    OpusAuthError,
    OpusConnectionError,
    OpusGatewayClient,
    OpusResponseError,
    OpusTimeoutError,
)
from tests.fake_gateway import FakeGateway, device_object

INTEGRATION = Path(__file__).parents[1] / "custom_components" / "opus_greennet"
STATE = "/devices/DEV1/state"
BODY = {"state": {"functions": [{"key": "switch", "value": "on"}]}}


@pytest.fixture
async def session():
    async with aiohttp.ClientSession() as client_session:
        yield client_session


def _client(
    session: aiohttp.ClientSession, gateway: FakeGateway, **overrides: str
) -> OpusGatewayClient:
    gateway.devices = [device_object("DEV1", "D2-01-01")]
    config = {**gateway.config, **overrides}
    return OpusGatewayClient(
        session,
        config["host"],
        config["port"],
        config["username"],
        config["password"],
    )


# ── B-1: HTTP status and JSON status are evaluated separately ──────────────


async def test_http_error_with_json_success_is_rejected(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    """Reproducer for B-1: an HTTP 500 must not pass because the body says 200."""
    gateway.http_status[STATE] = 500
    client = _client(session, gateway)
    with pytest.raises(OpusResponseError) as raised:
        await client.async_put(STATE, BODY)
    assert raised.value.http_status == 500
    assert raised.value.gateway_status == 200
    assert raised.value.status == 500
    assert raised.value.reason == "HTTP status 500, gateway status 200"


async def test_http_success_with_json_error_is_rejected(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    gateway.responses[STATE] = {
        "header": {"httpStatus": 400, "message": "no matching group found"}
    }
    client = _client(session, gateway)
    with pytest.raises(OpusResponseError) as raised:
        await client.async_put(STATE, BODY)
    assert raised.value.http_status == 200
    assert raised.value.gateway_status == 400
    assert raised.value.status == 400
    assert raised.value.reason == "status 400: no matching group found"


async def test_matching_error_statuses_keep_the_gateway_message(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    gateway.status[STATE] = 500
    client = _client(session, gateway)
    with pytest.raises(OpusResponseError) as raised:
        await client.async_put(STATE, BODY)
    assert raised.value.reason == "status 500: forced failure"
    assert (raised.value.status, raised.value.http_status) == (500, 500)
    assert raised.value.gateway_status == 500


async def test_http_error_with_differing_json_error_reports_both(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    gateway.responses[STATE] = {"header": {"httpStatus": 404, "message": "unknown"}}
    gateway.http_status[STATE] = 502
    client = _client(session, gateway)
    with pytest.raises(OpusResponseError) as raised:
        await client.async_put(STATE, BODY)
    assert raised.value.reason == "HTTP status 502, gateway status 404: unknown"
    assert (raised.value.status, raised.value.gateway_status) == (502, 404)


@pytest.mark.parametrize(
    "body",
    ["", "<html>Bad Gateway</html>", "[]", '{"header": {"httpStatus": true}}'],
)
async def test_http_error_without_usable_document_reports_http_status(
    session: aiohttp.ClientSession, gateway: FakeGateway, body: str
) -> None:
    """A proxy page or an empty body must not be reported as invalid JSON."""
    gateway.responses["/system/uptime"] = body
    gateway.http_status["/system/uptime"] = 502
    client = _client(session, gateway)
    with pytest.raises(OpusResponseError) as raised:
        await client.async_get("/system/uptime")
    assert raised.value.reason == "status 502"
    assert raised.value.http_status == 502
    assert raised.value.gateway_status is None


@pytest.mark.parametrize(
    ("http_status", "json_status"),
    [(200, 200), (200, 201), (201, 201), (202, 200), (201, 200)],
)
async def test_any_http_success_defers_to_the_json_status(
    session: aiohttp.ClientSession,
    gateway: FakeGateway,
    http_status: int,
    json_status: int,
) -> None:
    """2xx is transport success; 200/201 in the body decide the acknowledgement."""
    gateway.responses[STATE] = {"header": {"httpStatus": json_status}}
    gateway.http_status[STATE] = http_status
    client = _client(session, gateway)
    ack = await client.async_put(STATE, BODY)
    assert api.response_status(ack) == json_status


async def test_decode_errors_carry_the_http_status(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    gateway.responses["/system/info"] = "not json"
    client = _client(session, gateway)
    with pytest.raises(OpusResponseError, match="invalid JSON") as raised:
        await client.async_get("/system/info")
    assert raised.value.http_status == 200
    assert raised.value.gateway_status is None


def test_response_error_defaults() -> None:
    err = OpusResponseError("status 400", 400)
    assert (err.reason, err.status) == ("status 400", 400)
    assert err.http_status is None
    assert err.gateway_status is None


async def test_stream_error_status_carries_the_http_status(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    gateway.status["/devices/stream"] = 503
    client = _client(session, gateway)
    with pytest.raises(OpusResponseError, match="status 503") as raised:
        async for _ in client.async_stream():
            pass  # pragma: no cover
    assert raised.value.http_status == 503


# ── No client-side retries ─────────────────────────────────────────────────


@pytest.mark.parametrize(("method", "path"), [("PUT", STATE), ("GET", "/devices")])
async def test_timed_out_request_is_sent_exactly_once(
    session: aiohttp.ClientSession, gateway: FakeGateway, method: str, path: str
) -> None:
    """A timeout is reported, never repeated: a PUT may already have reached
    the actuator, and a repeat would add radio traffic."""
    gateway.delays[path] = 0.3
    client = _client(session, gateway)
    with pytest.raises(OpusTimeoutError):
        if method == "PUT":
            await client.async_put(path, BODY, timeout=0.05)
        else:
            await client.async_get(path, timeout=0.05)
    await asyncio.sleep(0.4)
    assert len(gateway.requests_to(method, path)) == 1


async def test_rejected_put_is_sent_exactly_once(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    gateway.status[STATE] = 500
    client = _client(session, gateway)
    with pytest.raises(OpusResponseError):
        await client.async_put(STATE, BODY)
    assert len(gateway.requests_to("PUT", STATE)) == 1


# ── Error texts never contain credentials ──────────────────────────────────


def _error_texts(err: BaseException) -> str:
    """Return the text of an error and its whole cause chain."""
    texts: list[str] = []
    current: BaseException | None = err
    while current is not None:
        texts.extend((str(current), repr(current)))
        current = current.__cause__ or current.__context__
    return "\n".join(texts)


async def test_error_texts_never_contain_credentials(
    session: aiohttp.ClientSession, gateway: FakeGateway
) -> None:
    username, password = "installer-user", "s3cr3t-Passw0rd"
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    client = _client(session, gateway, username=username, password=password)
    errors: list[OpusApiError] = []

    async def collect(request) -> None:
        try:
            await request
        except OpusApiError as err:
            errors.append(err)

    async def stream() -> None:
        async for _ in client.async_stream():
            pass  # pragma: no cover

    # Rejected credentials (the fake gateway expects other ones).
    await collect(client.async_get("/system/info"))
    await collect(stream())
    gateway.username, gateway.password = username, password
    # Gateway errors and timeouts.
    gateway.status[STATE] = 500
    await collect(client.async_put(STATE, BODY))
    gateway.http_status["/devices"] = 500
    await collect(client.async_get("/devices"))
    gateway.delays["/system/uptime"] = 0.3
    await collect(client.async_get("/system/uptime", timeout=0.05))
    # An unreachable gateway.
    await gateway.stop()
    await collect(client.async_get("/system/info"))
    await collect(stream())

    assert [type(err) for err in errors] == [
        OpusAuthError,
        OpusAuthError,
        OpusResponseError,
        OpusResponseError,
        OpusTimeoutError,
        OpusConnectionError,
        OpusConnectionError,
    ]
    for err in errors:
        text = _error_texts(err)
        assert password not in text
        assert username not in text
        assert token not in text


# ── Library boundary of api.py and wire.py (B-6, E5) ───────────────────────


def _imports(module: Path) -> tuple[set[str], set[str]]:
    """Return the absolute top-level packages and relative modules imported."""
    absolute: set[str] = set()
    relative: set[str] = set()
    for node in ast.walk(ast.parse(module.read_text())):
        if isinstance(node, ast.Import):
            absolute.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                relative.add(node.module or "")
            elif node.module:
                absolute.add(node.module.split(".")[0])
    return absolute, relative


@pytest.mark.parametrize(
    ("name", "allowed_absolute", "allowed_relative"),
    [("api.py", {"aiohttp"}, {"wire"}), ("wire.py", set(), set())],
)
def test_client_modules_import_no_home_assistant_or_integration_code(
    name: str, allowed_absolute: set[str], allowed_relative: set[str]
) -> None:
    """``api.py`` and ``wire.py`` can become a standalone library unchanged."""
    absolute, relative = _imports(INTEGRATION / name)
    third_party = absolute - set(sys.stdlib_module_names) - {"__future__"}
    assert third_party <= allowed_absolute
    assert "homeassistant" not in absolute
    assert relative <= allowed_relative


def test_integration_never_creates_its_own_client_session() -> None:
    """Every HTTP request uses Home Assistant's shared session (inject-websession)."""
    offenders = []
    for module in sorted(INTEGRATION.glob("*.py")):
        for node in ast.walk(ast.parse(module.read_text())):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", None)
            )
            if name in {"ClientSession", "TCPConnector"}:
                offenders.append(f"{module.name}:{node.lineno}")
    assert offenders == []


def test_client_never_closes_the_injected_session() -> None:
    tree = ast.parse((INTEGRATION / "api.py").read_text())
    closes = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "close"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "_session"
    ]
    assert closes == []
