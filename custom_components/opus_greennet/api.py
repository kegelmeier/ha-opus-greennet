"""Client for the OPUS gateway's EnOcean over IP REST API.

The gateway serves every endpoint below on one HTTP port (8080 by default)
with basic authentication. Responses are JSON objects carrying their own
``header.httpStatus``. Both statuses are checked: the HTTP status line must
report success (2xx) and the document status must be 200 or 201 (phase R3,
finding B-1). ``GET /devices/stream`` keeps the response open and writes one
JSON document per event: first the complete device list, then every telegram
and device change as it happens.

This module only speaks HTTP. It knows nothing about Home Assistant entities
and never repeats a request; the coordinator maps its exceptions to
translated errors.
"""

from __future__ import annotations

import asyncio
import base64
import codecs
import heapq
import itertools
from collections.abc import AsyncGenerator, Mapping
from math import isfinite
from typing import Final
from urllib.parse import quote

import aiohttp

from .wire import JsonObject, as_object, parse_json

# Bounds for one request/response exchange (seconds).
REQUEST_TIMEOUT: Final = 10.0
# ReCom reads travel over the radio to the device and back; the gateway
# answers them after several seconds.
RECOM_TIMEOUT: Final = 45.0
CONNECT_TIMEOUT: Final = 10.0
# A healthy stream can stay silent for a long time when nothing changes.
# After this many seconds without data it is reopened; the fresh device list
# that every new stream starts with also resynchronises all state.
STREAM_IDLE_TIMEOUT: Final = 900.0
# Upper bound for one streamed JSON document (the full device list of a large
# installation is a few hundred kilobytes).
MAX_DOCUMENT_SIZE: Final = 8 * 1024 * 1024

# Queue priorities: lower values are dispatched first. User-initiated control
# commands must never wait behind status queries, and delayed (background)
# reconciliation queries must never compete with either of them.
PRIORITY_COMMAND: Final = 0
PRIORITY_QUERY: Final = 10
PRIORITY_BACKGROUND: Final = 20


class OpusApiError(Exception):
    """Base class for gateway API failures."""


class OpusConnectionError(OpusApiError):
    """The gateway could not be reached or did not answer in time."""


class OpusTimeoutError(OpusConnectionError):
    """The request was sent but its answer did not arrive in time."""


class OpusAuthError(OpusApiError):
    """The gateway rejected the credentials."""


class OpusResponseError(OpusApiError):
    """The gateway answered with an error status or an invalid document.

    ``status`` is the status that failed: the HTTP status when the status line
    reported an error, otherwise the document status. ``http_status`` and
    ``gateway_status`` keep both values separately (``None`` when unknown, for
    example an unreadable document).
    """

    def __init__(
        self,
        reason: str,
        status: int | None = None,
        *,
        http_status: int | None = None,
        gateway_status: int | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.http_status = http_status
        self.gateway_status = gateway_status


def response_status(data: JsonObject) -> int:
    """Return ``header.httpStatus`` as an integer.

    Accepted representations are an int, an integral float or a numeric
    string (for example 200, 200.0 or "200"). Booleans, fractional or
    non-finite floats and other types raise ValueError.
    """
    header = as_object(data.get("header"))
    if header is None:
        raise ValueError("The gateway response is missing its status")
    raw_status = header.get("httpStatus")
    if isinstance(raw_status, bool) or not isinstance(raw_status, int | float | str):
        raise ValueError("The gateway returned an invalid status")
    if isinstance(raw_status, float) and not raw_status.is_integer():
        raise ValueError("The gateway returned an invalid status")
    try:
        return int(raw_status)
    except (ValueError, OverflowError) as err:
        raise ValueError("The gateway returned an invalid status") from err


def decode_response(text: str, *, require_status: bool = True) -> JsonObject:
    """Validate a gateway document and reject a negative status.

    Status 201 is the SoftSmartAck acceptance of the protocol and counts as
    success like 200.
    """
    try:
        decoded = parse_json(text)
    except ValueError as err:
        raise OpusResponseError("The gateway returned invalid JSON") from err
    data = as_object(decoded)
    if not data:
        raise OpusResponseError("The gateway returned an invalid response object")
    if data.get("header") is not None or require_status:
        try:
            status = response_status(data)
        except ValueError as err:
            raise OpusResponseError(str(err)) from err
        if status not in (200, 201):
            raise OpusResponseError(
                _error_reason(data, status), status, gateway_status=status
            )
    return data


def _error_reason(data: JsonObject, status: int) -> str:
    """Return the gateway's own error text for a rejected request."""
    message = _gateway_message(data)
    return f"status {status}: {message}" if message else f"status {status}"


def _gateway_message(data: JsonObject) -> str:
    header = as_object(data.get("header")) or {}
    message = header.get("message")
    return message if isinstance(message, str) else ""


def _http_error(text: str, http_status: int) -> OpusResponseError:
    """Describe an answer whose HTTP status line reports a failure.

    The HTTP status decides; the document status and message are kept when
    the body is a readable gateway document. A body that is not one (empty,
    a proxy error page) still yields the HTTP status instead of "invalid JSON".
    """
    gateway_status: int | None = None
    message = ""
    try:
        data = as_object(parse_json(text)) if text.strip() else None
    except ValueError:
        data = None
    if data:
        message = _gateway_message(data)
        try:
            gateway_status = response_status(data)
        except ValueError:
            gateway_status = None
    if gateway_status is None or gateway_status == http_status:
        reason = f"status {http_status}"
    else:
        reason = f"HTTP status {http_status}, gateway status {gateway_status}"
    if message:
        reason = f"{reason}: {message}"
    return OpusResponseError(
        reason, http_status, http_status=http_status, gateway_status=gateway_status
    )


def gateway_uptime_value(data: JsonObject) -> str:
    """Read and validate the gateway's system uptime response."""
    response = as_object(data.get("systemUptimeResponse"))
    value = response.get("uptime") if response is not None else None
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        raise OpusResponseError("The gateway returned an invalid uptime")
    try:
        uptime = float(value)
    except (ValueError, OverflowError) as err:
        raise OpusResponseError("The gateway returned an invalid uptime") from err
    if not isfinite(uptime) or uptime < 0:
        raise OpusResponseError("The gateway returned an invalid uptime")
    return str(value)


class JsonDocumentSplitter:
    """Cut a stream of back-to-back JSON documents into complete documents.

    The gateway writes pretty-printed objects without any delimiter between
    them. The splitter tracks nesting and string state character by character,
    so each document is parsed exactly once and partial chunks are buffered.
    Every document is limited to ``max_size`` characters, whether it arrives in
    one chunk or grows across several (phase R4).
    """

    def __init__(self, max_size: int = MAX_DOCUMENT_SIZE) -> None:
        self._max_size = max_size
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._parts: list[str] = []
        self._size = 0
        self._depth = 0
        self._in_string = False
        self._escape = False

    def feed(self, chunk: bytes) -> list[str]:
        """Return every document completed by ``chunk``."""
        text = self._decoder.decode(chunk)
        documents: list[str] = []
        # A document left open by the previous chunk continues at index 0.
        start: int | None = 0 if self._depth else None
        for index, char in enumerate(text):
            if self._depth == 0:
                if char in "{[":
                    start = index
                    self._depth = 1
                elif not char.isspace():
                    raise OpusResponseError("The event stream contains invalid data")
                continue
            if self._in_string:
                if self._escape:
                    self._escape = False
                elif char == "\\":
                    self._escape = True
                elif char == '"':
                    self._in_string = False
            elif char == '"':
                self._in_string = True
            elif char in "{[":
                self._depth += 1
            elif char in "}]":
                self._depth -= 1
                if self._depth == 0:
                    self._parts.append(text[start : index + 1])
                    document = "".join(self._parts)
                    self._parts.clear()
                    self._size = 0
                    start = None
                    # A complete document inside one chunk is bounded too, not
                    # only a remainder growing across chunks (finding B-3).
                    if len(document) > self._max_size:
                        raise OpusResponseError("An event stream document is too large")
                    documents.append(document)
        if start is not None:
            tail = text[start:]
            self._parts.append(tail)
            self._size += len(tail)
            if self._size > self._max_size:
                raise OpusResponseError("An event stream document is too large")
        return documents


class PriorityLock:
    """An asyncio lock whose waiters are woken by priority, then FIFO."""

    def __init__(self) -> None:
        self._locked = False
        self._waiters: list[tuple[int, int, asyncio.Future[None]]] = []
        self._counter = itertools.count()

    @property
    def queue_depth(self) -> int:
        """Return the number of waiters."""
        return len(self._waiters)

    def locked(self) -> bool:
        """Return whether the lock is currently held."""
        return self._locked

    async def acquire(self, priority: int = PRIORITY_COMMAND) -> None:
        """Acquire the lock, queueing by priority while it is held."""
        if not self._locked and not self._waiters:
            self._locked = True
            return
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        entry = (priority, next(self._counter), future)
        heapq.heappush(self._waiters, entry)
        try:
            await future
        except BaseException:
            if future.done() and not future.cancelled():
                # Ownership was handed over just before cancellation.
                self.release()
            else:
                self._waiters.remove(entry)
                heapq.heapify(self._waiters)
            raise

    def release(self) -> None:
        """Hand the lock to the highest-priority waiter, if any."""
        while self._waiters:
            _, _, future = heapq.heappop(self._waiters)
            if not future.done():
                # Ownership transfers directly; the lock stays held.
                future.set_result(None)
                return
        self._locked = False


class OpusGatewayClient:
    """HTTP access to one gateway."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        username: str,
        password: str,
    ) -> None:
        self._session = session
        self.host = host
        self.port = port
        # IPv6 literals need brackets inside a URL.
        url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        self._base_url = f"http://{url_host}:{port}"
        credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
        self._headers = {"Authorization": f"Basic {credentials}"}

    @staticmethod
    def device_path(device_id: str, *parts: str) -> str:
        """Return a device resource path with the identifier safely quoted."""
        return "/".join(("/devices", quote(device_id, safe=""), *parts))

    async def async_get(
        self, path: str, *, timeout: float = REQUEST_TIMEOUT
    ) -> JsonObject:
        """GET one JSON document."""
        return await self._async_request("GET", path, None, timeout)

    async def async_put(
        self,
        path: str,
        body: Mapping[str, object],
        *,
        timeout: float = REQUEST_TIMEOUT,
    ) -> JsonObject:
        """PUT a JSON body and return the acknowledgement document."""
        return await self._async_request("PUT", path, body, timeout)

    async def _async_request(
        self,
        method: str,
        path: str,
        body: Mapping[str, object] | None,
        timeout: float,
    ) -> JsonObject:
        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                headers=self._headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=timeout, connect=CONNECT_TIMEOUT),
            ) as response:
                _raise_for_auth(response)
                text = await response.text(errors="replace")
        except TimeoutError as err:
            raise OpusTimeoutError(f"{method} {path} timed out") from err
        except aiohttp.ClientError as err:
            raise OpusConnectionError(f"{method} {path} failed: {err}") from err
        # Transport success (2xx) and gateway success (document status 200 or
        # 201) are separate conditions; both must hold (B-1). The request is
        # never repeated, whatever the outcome.
        if not 200 <= response.status < 300:
            raise _http_error(text, response.status)
        try:
            return decode_response(text)
        except OpusResponseError as err:
            err.http_status = response.status
            raise

    async def async_stream(self) -> AsyncGenerator[JsonObject]:
        """Yield every document of ``GET /devices/stream`` until it ends.

        Closing the generator (``aclose``) releases the HTTP response at once.
        """
        splitter = JsonDocumentSplitter()
        try:
            async with self._session.get(
                f"{self._base_url}/devices/stream",
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(
                    total=None,
                    connect=CONNECT_TIMEOUT,
                    sock_connect=CONNECT_TIMEOUT,
                    sock_read=STREAM_IDLE_TIMEOUT,
                ),
            ) as response:
                _raise_for_auth(response)
                if response.status != 200:
                    raise OpusResponseError(
                        f"status {response.status}",
                        response.status,
                        http_status=response.status,
                    )
                async for chunk in response.content.iter_any():
                    for text in splitter.feed(chunk):
                        document = as_object(parse_json(text))
                        if document is not None:
                            yield document
        except TimeoutError as err:
            raise OpusTimeoutError("The event stream went silent") from err
        except aiohttp.ClientError as err:
            raise OpusConnectionError(f"The event stream failed: {err}") from err
        except ValueError as err:
            raise OpusResponseError("The event stream contains invalid JSON") from err


def _raise_for_auth(response: aiohttp.ClientResponse) -> None:
    if response.status in (401, 403):
        raise OpusAuthError(f"status {response.status}")
