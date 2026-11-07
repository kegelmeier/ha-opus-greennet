"""An in-process OPUS gateway serving the REST API over real HTTP."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

EURID = "AABB0011"
PASSWORD = EURID


def device_object(
    device_id: str,
    eep: str | list[str],
    *,
    friendly_id: str | None = None,
    states: list[dict[str, Any]] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Return a device object as the gateway lists it."""
    eeps = [eep] if isinstance(eep, str) else eep
    return {
        "deviceId": device_id,
        "friendlyId": friendly_id or f"Device {device_id}",
        "eeps": [{"eep": name, "version": 0.9, "direction": "both"} for name in eeps],
        "manufacturer": "Jaeger Direkt",
        "states": states or [],
        **fields,
    }


def telegram(
    device_id: str,
    functions: list[dict[str, Any]],
    *,
    direction: str = "from",
    **fields: Any,
) -> dict[str, Any]:
    """Return a telegram document as the event stream delivers it."""
    return {
        "header": {"content": "telegram", "gateway": "OPUS-GW test"},
        "telegram": {
            "deviceId": device_id,
            "friendlyId": f"Device {device_id}",
            "timestamp": "2026-10-19T18:00:00.000+0200",
            "direction": direction,
            "functions": functions,
            **fields,
        },
    }


class FakeGateway:
    """Serve the endpoints the integration uses and record every request.

    Behaviour is configured through attributes: ``devices``, ``uptime``,
    ``status`` (per path, a forced HTTP status), ``delays`` (per path, seconds
    before answering), ``responses`` (per path, a JSON body override),
    ``http_status`` (per path, the HTTP status line of an otherwise unchanged
    answer, so it can differ from ``header.httpStatus`` in the body),
    ``close_after_snapshot`` (end every event stream right after its device
    list), ``stream_without_configuration`` (stream the device list without
    the ``configuration`` block, like real gateways), ``snapshot_delay``
    (seconds between opening a stream and writing its device list),
    ``redirects`` (per path, the target of a 302 answer), ``chunked`` (paths
    answered without a Content-Length) and ``accept_auth`` (reject every
    request with 401 when False), ``stream_framing`` (how a stream answers the
    ``delimited=newLine`` parameter: ``"legacy"`` ignores it like older
    firmware and writes pretty-printed documents without delimiter, ``"ndjson"``
    writes one document per line ending in CRLF, ``"reject"`` answers
    ``reject_status``, by default HTTP 400) and ``stream_queries`` (the query
    string of every stream request) and ``drop_connection`` (paths whose request
    the gateway reads and records, then answers by closing the connection
    without a response, like a gateway that goes down right after taking a
    command).
    """

    def __init__(self, eurid: str = EURID, password: str = PASSWORD) -> None:
        self.eurid = eurid
        self.username = "admin"
        self.password = password
        self.devices: list[dict[str, Any]] = []
        # Gateway profiles per device ID (GET /devices/<id>/profile); devices
        # without an entry answer like a gateway without the endpoint.
        self.profiles: dict[str, dict[str, Any]] = {}
        self.uptime: int | str = 3600
        self.system_info: dict[str, Any] = {}
        self.accept_auth = True
        self.status: dict[str, int] = {}
        self.delays: dict[str, float] = {}
        self.responses: dict[str, Any] = {}
        self.http_status: dict[str, int] = {}
        self.redirects: dict[str, str] = {}
        self.chunked: set[str] = set()
        self.drop_connection: set[str] = set()
        self.requests: list[tuple[str, str, Any]] = []
        self.on_request: Callable[[str, str, Any], Awaitable[None]] | None = None
        self.stream_preamble: list[str] = []
        self.close_after_snapshot = False
        self.stream_framing = "legacy"
        self.reject_status = 400
        self.stream_queries: list[str] = []
        self.snapshot_delay = 0.0
        # ``/system/receivemode``: GET reads, POST sets (the handler in the
        # gateway software answers a POST like a GET). Field ``receiveMode``,
        # values ``learnMode``/``normalMode``. Taken from the handler, not from a
        # recording. ``receive_mode_ignores_post`` models a gateway that accepts
        # a write without applying it.
        self.receive_mode = "normalMode"
        self.receive_mode_ignores_post = False
        # ``DELETE /devices/<id>`` (handler in the gateway software; the answer
        # shape is an assumption). ``delete_rejection`` is an error header the
        # gateway answers instead of deleting; ``delete_ignored`` models a
        # gateway that answers success but keeps the device.
        self.delete_rejection: dict[str, Any] | None = None
        self.delete_ignored = False
        # Real gateways stream the device list without ``configuration``
        # (phase 21 hardware diagnostics); GET /devices carries it.
        self.stream_without_configuration = False
        self._streams: list[asyncio.Queue[str | None]] = []
        self._stream_connected = asyncio.Event()
        self._stopping = False
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self.host = "127.0.0.1"
        self.port = 0

    # ── lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Listen on a free local port (the same port again after stop)."""
        app = web.Application()
        app.router.add_route("*", "/{path:.*}", self._handle)
        self._stopping = False
        self._runner = web.AppRunner(app, handle_signals=False, shutdown_timeout=1)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        if self.port == 0:
            server = self._site._server  # noqa: SLF001 - read the bound port
            assert server is not None
            self.port = server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        """Close all streams and stop listening (connections are refused)."""
        self._stopping = True
        await self.close_streams()
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None

    @property
    def config(self) -> dict[str, Any]:
        """Return config flow input for this gateway."""
        return {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password": self.password,
        }

    # ── stream control ─────────────────────────────────────────────────────

    @property
    def stream_count(self) -> int:
        """Return the number of open event streams."""
        return len(self._streams)

    async def wait_for_stream(self, timeout: float = 5) -> None:
        """Wait until at least one event stream is connected."""
        async with asyncio.timeout(timeout):
            while not self._streams:
                self._stream_connected.clear()
                await self._stream_connected.wait()

    async def push(self, document: dict[str, Any] | str) -> None:
        """Send a document (or raw text) to every open stream."""
        text = document if isinstance(document, str) else json.dumps(document)
        for queue in list(self._streams):
            await queue.put(text)
        # Let the server write and the client parse it.
        for _ in range(5):
            await asyncio.sleep(0)
        await asyncio.sleep(0.01)

    async def close_streams(self) -> None:
        """End every open stream cleanly."""
        for queue in list(self._streams):
            await queue.put(None)
        for _ in range(50):
            if not self._streams:
                break
            await asyncio.sleep(0.01)

    def devices_document(self) -> dict[str, Any]:
        return {
            "header": {"httpStatus": 200, "content": "devices"},
            "devices": self.devices,
        }

    def requests_to(self, method: str, path: str) -> list[Any]:
        """Return the bodies of all recorded requests to one endpoint."""
        return [body for m, p, body in self.requests if m == method and p == path]

    # ── HTTP ───────────────────────────────────────────────────────────────

    def _authorized(self, request: web.Request) -> bool:
        expected = base64.b64encode(
            f"{self.username}:{self.password}".encode()
        ).decode()
        return self.accept_auth and request.headers.get("Authorization") == (
            f"Basic {expected}"
        )

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        response = await self._answer_request(request)
        path = f"/{request.match_info['path']}"
        if (code := self.http_status.get(path)) is not None and not response.prepared:
            response.set_status(code)
        return response

    async def _answer_request(self, request: web.Request) -> web.StreamResponse:
        path = f"/{request.match_info['path']}"
        body = await request.json() if request.can_read_body else None
        self.requests.append((request.method, path, body))
        if path in self.drop_connection and request.transport is not None:
            request.transport.close()
            return web.Response(status=500)
        if self.on_request is not None:
            await self.on_request(request.method, path, body)
        if not self._authorized(request):
            return web.Response(status=401)
        if (target := self.redirects.get(path)) is not None:
            return web.Response(status=302, headers={"Location": target})
        if (delay := self.delays.get(path)) is not None:
            await asyncio.sleep(delay)
        if (status := self.status.get(path)) is not None:
            return web.json_response(
                {"header": {"httpStatus": status, "message": "forced failure"}},
                status=status,
            )
        if path == "/devices/stream":
            if self._stopping:
                return web.Response(status=503)
            self.stream_queries.append(request.query_string)
            if self.stream_framing == "reject" and "delimited" in request.query:
                return web.json_response(
                    {
                        "header": {
                            "httpStatus": self.reject_status,
                            "message": "bad parameter",
                        }
                    },
                    status=self.reject_status,
                )
            return await self._stream(request)
        if path in self.responses:
            response = self.responses[path]
            if isinstance(response, str):
                answer = web.Response(text=response, content_type="application/json")
            else:
                answer = web.json_response(response)
            if path in self.chunked:
                answer.enable_chunked_encoding()
            return answer
        return web.json_response(self._answer(request.method, path, body))

    def _answer(self, method: str, path: str, body: Any = None) -> dict[str, Any]:
        ok = {"httpStatus": 200, "gateway": "OPUS-GW test"}
        if path == "/system/info":
            return {
                "header": {**ok, "content": "systemInfo"},
                "systemInfo": {
                    "version": "OPUS-GW test",
                    "eurid": self.eurid,
                    "homekitPin": "111-22-333",
                    "homekitQR": "X-HM://0TESTSETUPPAYLOAD",
                    **self.system_info,
                },
            }
        if path == "/system/uptime":
            return {
                "header": {**ok, "content": "Uptime"},
                "systemUptimeResponse": {"uptime": self.uptime},
            }
        if path == "/system/receivemode":
            if method == "POST" and not self.receive_mode_ignores_post:
                self.receive_mode = (body or {}).get("receiveMode", self.receive_mode)
            return {
                "header": {**ok, "content": "receiveMode"},
                "receiveMode": self.receive_mode,
            }
        if path == "/devices":
            return self.devices_document()
        parts = path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "devices":
            device = next((d for d in self.devices if d["deviceId"] == parts[1]), None)
            if device is None:
                return {"header": {"httpStatus": 404, "message": "unknown device"}}
            if len(parts) == 2 and method == "DELETE":
                if self.delete_rejection is not None:
                    return {"header": self.delete_rejection}
                if not self.delete_ignored:
                    self.devices.remove(device)
                return {"header": ok}
            if len(parts) == 2:
                return {"header": {**ok, "content": "device"}, "device": device}
            if parts[2] == "configuration":
                # The device's configuration: a PUT stores its parameters (like
                # a device that accepts every value), a GET reads them back.
                stored = device.setdefault("configuration", {}).setdefault(
                    "parameters", []
                )
                if method == "PUT":
                    written = (
                        (body or {}).get("configuration", {}).get("parameters", [])
                    )
                    for parameter in written:
                        known = next(
                            (p for p in stored if p["key"] == parameter["key"]), None
                        )
                        if known is None:
                            stored.append(dict(parameter))
                        else:
                            known["value"] = parameter["value"]
                    return {"header": ok}
                return {
                    "header": {**ok, "content": "configuration"},
                    "configuration": {"deviceId": parts[1], "parameters": stored},
                }
            if method == "PUT":
                return {"header": ok}
            if parts[2] == "profile" and parts[1] in self.profiles:
                return {
                    "header": {**ok, "content": "profile"},
                    "profile": self.profiles[parts[1]],
                }
            if parts[2] == "parameters":
                return {"header": {**ok, "content": "parameters"}, "parameters": []}
        return {"header": {"httpStatus": 404, "message": "not found"}}

    def _line_framed(self, request: web.Request) -> bool:
        return self.stream_framing == "ndjson" and (
            request.query.get("delimited") == "newLine"
        )

    async def _stream(self, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(
            headers={"Content-Type": "application/json;charset=UTF-8"}
        )
        await response.prepare(request)
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        for text in self.stream_preamble:
            await response.write(text.encode())
        if self.snapshot_delay:
            # The stream is open (headers sent) but the list is not written yet.
            await asyncio.sleep(self.snapshot_delay)
        # Pretty-printed like the real gateway, without delimiters.
        snapshot = self.devices_document()
        if self.stream_without_configuration:
            snapshot["devices"] = [
                {key: value for key, value in device.items() if key != "configuration"}
                for device in snapshot["devices"]
            ]
        if self._line_framed(request):
            await response.write(json.dumps(snapshot).encode() + b"\r\n")
        else:
            await response.write(json.dumps(snapshot, indent=2).encode())
        if self.close_after_snapshot:
            # A gateway that ends every stream right after the device list.
            await response.write_eof()
            return response
        self._streams.append(queue)
        self._stream_connected.set()
        try:
            line_framed = self._line_framed(request)
            while (text := await queue.get()) is not None:
                if line_framed and not text.endswith("\n"):
                    text += "\r\n"
                await response.write(text.encode())
        except ConnectionResetError, asyncio.CancelledError:
            pass
        finally:
            self._streams.remove(queue)
        return response
