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
    (seconds between opening a stream and writing its device list) and
    ``accept_auth`` (reject every request with 401 when False).
    """

    def __init__(self, eurid: str = EURID, password: str = PASSWORD) -> None:
        self.eurid = eurid
        self.username = "admin"
        self.password = password
        self.devices: list[dict[str, Any]] = []
        self.uptime: int | str = 3600
        self.system_info: dict[str, Any] = {}
        self.accept_auth = True
        self.status: dict[str, int] = {}
        self.delays: dict[str, float] = {}
        self.responses: dict[str, Any] = {}
        self.http_status: dict[str, int] = {}
        self.requests: list[tuple[str, str, Any]] = []
        self.on_request: Callable[[str, str, Any], Awaitable[None]] | None = None
        self.stream_preamble: list[str] = []
        self.close_after_snapshot = False
        self.snapshot_delay = 0.0
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
        if self.on_request is not None:
            await self.on_request(request.method, path, body)
        if not self._authorized(request):
            return web.Response(status=401)
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
            return await self._stream(request)
        if path in self.responses:
            response = self.responses[path]
            if isinstance(response, str):
                return web.Response(text=response, content_type="application/json")
            return web.json_response(response)
        return web.json_response(self._answer(request.method, path))

    def _answer(self, method: str, path: str) -> dict[str, Any]:
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
        if path == "/devices":
            return self.devices_document()
        parts = path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "devices":
            device = next((d for d in self.devices if d["deviceId"] == parts[1]), None)
            if device is None:
                return {"header": {"httpStatus": 404, "message": "unknown device"}}
            if len(parts) == 2:
                return {"header": {**ok, "content": "device"}, "device": device}
            if method == "PUT":
                return {"header": ok}
            if parts[2] == "configuration":
                return {
                    "header": {**ok, "content": "configuration"},
                    "configuration": {"deviceId": parts[1], "parameters": []},
                }
            if parts[2] == "parameters":
                return {"header": {**ok, "content": "parameters"}, "parameters": []}
        return {"header": {"httpStatus": 404, "message": "not found"}}

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
        await response.write(json.dumps(snapshot, indent=2).encode())
        if self.close_after_snapshot:
            # A gateway that ends every stream right after the device list.
            await response.write_eof()
            return response
        self._streams.append(queue)
        self._stream_connected.set()
        try:
            while (text := await queue.get()) is not None:
                await response.write(text.encode())
        except ConnectionResetError, asyncio.CancelledError:
            pass
        finally:
            self._streams.remove(queue)
        return response
