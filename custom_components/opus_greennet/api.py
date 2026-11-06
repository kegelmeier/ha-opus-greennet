"""Client for the OPUS gateway's EnOcean over IP REST API.

Gateways before WebUI 2026 serve every endpoint below on one HTTP port (8080 by
default) with basic authentication. Gateways with WebUI 2026 serve the same
paths over HTTPS (port 443) with a user login and a token, and a self-signed
certificate that is pinned by its fingerprint (see ``OpusGatewayClient``).
Responses are JSON objects carrying their own
``header.httpStatus``. Both statuses are checked: the HTTP status line must
report success (2xx) and the document status must be 200, 201 or 202 (phase
R3, finding B-1). ``GET /devices/stream`` keeps the response open and writes one
JSON document per event: first the complete device list, then every telegram
and device change as it happens. The client asks for one document per line
(``delimited=newLine``) and falls back to the plain stream only when the
gateway refuses that parameter.

This module only speaks HTTP. It knows nothing about Home Assistant entities
and never repeats a request; the coordinator maps its exceptions to
translated errors.
"""

from __future__ import annotations

import asyncio
import base64
import codecs
import hashlib
import heapq
import itertools
import logging
import ssl
from collections.abc import AsyncGenerator, Mapping
from contextlib import aclosing
from math import isfinite
from typing import Final
from urllib.parse import quote

import aiohttp

from .wire import JsonObject, as_object, parse_json

_LOGGER = logging.getLogger(__name__)

# The two access profiles of the client. The integration stores them in its
# entry data (``const.py`` re-exports them); the client module itself must not
# import integration code.
GENERATION_LEGACY: Final = "legacy"
GENERATION_WEBUI_2026: Final = "webui2026"

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
# Upper bound for the body of one ordinary (non-stream) response.
MAX_RESPONSE_SIZE: Final = MAX_DOCUMENT_SIZE
_READ_CHUNK_SIZE: Final = 64 * 1024

_STREAM_PATH: Final = "/devices/stream"
# WebUI 2026 gateways. ASSUMPTION (not verified on a gateway of that
# generation): the login is ``POST /auth/login`` with the user name and the
# password as JSON, the answer carries the token as ``token``, and the token
# travels as a bearer token. Only ``/auth/setup-status`` is documented by the
# vendor. Everything that depends on these guesses is in this block.
_SETUP_STATUS_PATH: Final = "/auth/setup-status"
_LOGIN_PATH: Final = "/auth/login"
_LOGIN_TOKEN_KEY: Final = "token"
# Line-delimited stream: one JSON document per line, terminated by CRLF
# (hardware-verified on OPUS-IQ-DOT 1.21.30). ``direction``, ``output`` and
# ``levelOfDetail`` are deliberately not requested (docs/quality/phases).
_LINE_FRAMING_QUERY: Final = "delimited=newLine"
# A line-framed request answered with any other status than 200 is tried once
# more without the parameter, in the same attempt: firmware that does not know
# it may answer 400, 404 or 500 alike. Rejected credentials (401/403) and rate
# limiting (429) say nothing about the parameter and never fall back.
_NO_FALLBACK_STATUSES: Final = frozenset({429})

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
    """The request was sent but no answer arrived (timeout or broken connection).

    Whether the gateway acted on the request is unknown.
    """


class OpusAuthError(OpusApiError):
    """The gateway rejected the credentials."""


class OpusCertificateError(OpusAuthError):
    """The gateway's certificate is not the remembered one (or none is known).

    It is an authentication failure on purpose: the connection is only trusted
    again after the user confirmed the certificate.
    """


class OpusTokenExpiredError(OpusConnectionError):
    """The gateway rejected the access token (expired or revoked).

    The token is dropped and requested again with the next request; the request
    that met the rejection is not sent again.
    """


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


class _NotRepeated(aiohttp.ClientError):
    """aiohttp tried to send a request again after its connection broke."""


def _send_once() -> aiohttp.ClientMiddlewareType:
    """Return a middleware that stops aiohttp from sending a request again.

    aiohttp resends idempotent methods, ``PUT`` included, once when the
    connection breaks. The gateway may already have acted on the first request,
    so a command could run twice and a radio read would be repeated. The
    middleware runs once per attempt and fails the second.
    """
    attempts = 0

    async def middleware(
        request: aiohttp.ClientRequest, handler: aiohttp.ClientHandlerType
    ) -> aiohttp.ClientResponse:
        nonlocal attempts
        attempts += 1
        if attempts > 1:
            raise _NotRepeated
        return await handler(request)

    return middleware


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
    success like 200. Status 202 is the gateway storing a target state for a
    sleeping SoftSmartAck device (e.g. the HOPPE unlock answer), also success.
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
        if status not in (200, 201, 202):
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

    def finish(self) -> None:
        """Check that the stream ended between two documents.

        A complete last document needs no terminator: it is returned by
        ``feed`` as soon as its closing bracket arrives. What remains here is
        an unfinished document or stray bytes, which the stream must not drop
        silently.
        """
        tail = self._decoder.decode(b"", final=True)
        if self._depth or tail.strip():
            raise OpusResponseError("The event stream ended inside a document")

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


class _LineFramingRefused(Exception):
    """The gateway refused ``delimited=newLine`` before sending a document."""


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
    """HTTP access to one gateway.

    ``generation`` selects the access: ``GENERATION_LEGACY`` (plain HTTP, basic
    authentication) or ``GENERATION_WEBUI_2026`` (HTTPS pinned to
    ``fingerprint``, SHA-256 of the certificate as hexadecimal text, and a
    token). The token is requested with the first request, kept in memory only
    and requested again after the gateway rejected it. A rejected token is
    reported (``OpusTokenExpiredError``) and the request is never sent again.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        username: str,
        password: str,
        *,
        generation: str = GENERATION_LEGACY,
        fingerprint: str | None = None,
    ) -> None:
        self._session = session
        self._line_framing_rejected = False
        self._stream_framing: str | None = None
        self.host = host
        self.port = port
        self.generation = generation
        # IPv6 literals need brackets inside a URL.
        url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        self._token: str | None = None
        self._login_lock = asyncio.Lock()
        self._pin: aiohttp.Fingerprint | None = None
        if generation == GENERATION_WEBUI_2026:
            self._base_url = f"https://{url_host}:{port}"
            self._credentials: dict[str, str] | None = {
                "username": username,
                "password": password,
            }
            self._headers: dict[str, str] = {}
            self._pin = _pinned_fingerprint(fingerprint)
        else:
            self._base_url = f"http://{url_host}:{port}"
            self._credentials = None
            basic = base64.b64encode(f"{username}:{password}".encode()).decode()
            self._headers = {"Authorization": f"Basic {basic}"}

    @property
    def _tls(self) -> aiohttp.Fingerprint | bool:
        """Return the ``ssl`` option of a request: the pin, or the default."""
        if self.generation != GENERATION_WEBUI_2026:
            return True
        if self._pin is None:
            raise OpusCertificateError("The gateway certificate is not known")
        return self._pin

    async def _async_auth_headers(self) -> dict[str, str]:
        """Return the headers that authenticate a request, logging in if needed."""
        if self._credentials is None:
            return self._headers
        if self._token is None:
            async with self._login_lock:
                if self._token is None:
                    self._token = await self._async_login(self._credentials)
        return {"Authorization": f"Bearer {self._token}"}

    async def _async_login(self, credentials: Mapping[str, str]) -> str:
        """Request a token. The credentials never appear in an error text."""
        tls = self._tls
        try:
            async with self._session.post(
                f"{self._base_url}{_LOGIN_PATH}",
                json=credentials,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(
                    total=REQUEST_TIMEOUT, connect=CONNECT_TIMEOUT
                ),
                ssl=tls,
            ) as response:
                if response.status in (401, 403):
                    raise OpusAuthError(f"login rejected (status {response.status})")
                text = await _read_bounded_text(response)
                status = response.status
        except TimeoutError as err:
            raise OpusTimeoutError("login timed out") from err
        except aiohttp.ServerFingerprintMismatch as err:
            raise OpusCertificateError("The gateway certificate changed") from err
        except aiohttp.ClientError as err:
            raise OpusConnectionError(f"login failed: {err}") from err
        if not 200 <= status < 300:
            raise _http_error(text, status)
        try:
            document = as_object(parse_json(text)) if text.strip() else None
        except ValueError as err:
            raise OpusResponseError(
                "The gateway login returned no token", status
            ) from err
        token = None if document is None else document.get(_LOGIN_TOKEN_KEY)
        if not isinstance(token, str) or not token.strip():
            raise OpusResponseError("The gateway login returned no token", status)
        return token.strip()

    def _check_auth(self, response: aiohttp.ClientResponse) -> None:
        """Map 401/403. A token the gateway rejects is dropped, not retried."""
        if self._credentials is not None and response.status == 401:
            self._token = None
            raise OpusTokenExpiredError("The access token was rejected (status 401)")
        _raise_for_auth(response)

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
        headers = await self._async_auth_headers()
        # A command or a radio read is never sent again by aiohttp if the
        # connection breaks after the gateway may have taken it.
        radio = method != "GET" or timeout > REQUEST_TIMEOUT
        tls = self._tls
        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                headers=headers,
                json=body,
                middlewares=(_send_once(),) if radio else None,
                # The gateway never redirects. Following one could send the
                # Authorization header to another host.
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=timeout, connect=CONNECT_TIMEOUT),
                ssl=tls,
            ) as response:
                self._check_auth(response)
                text = await _read_bounded_text(response)
        except TimeoutError as err:
            raise OpusTimeoutError(f"{method} {path} timed out") from err
        except aiohttp.ServerFingerprintMismatch as err:
            raise OpusCertificateError("The gateway certificate changed") from err
        except _NotRepeated as err:
            raise OpusTimeoutError(
                f"{method} {path} lost its connection and is not sent again"
            ) from err
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

    @property
    def stream_framing(self) -> str | None:
        """Return the framing requested by the last accepted stream.

        ``"line"`` or ``"plain"``; ``None`` until a stream was accepted. This is
        the request, not a proof of the format: firmware that ignores the
        parameter answers in the plain format to a ``"line"`` request, and the
        same splitter reads both.
        """
        return self._stream_framing

    def reset_stream_framing(self) -> None:
        """Offer the line-delimited stream again on the next connection.

        Called when the gateway restarted: a firmware update that happened in
        between may have added what an older firmware rejected.
        """
        self._line_framing_rejected = False

    async def async_stream(self) -> AsyncGenerator[JsonObject]:
        """Yield every document of ``GET /devices/stream`` until it ends.

        The stream is first requested with ``delimited=newLine`` (one document
        per line, hardware-verified on 1.21.30). Firmware that ignores the
        parameter answers in the legacy format, which the same splitter cuts.
        Any other status than 200 (except rejected credentials and 429) gets
        one plain request in the same attempt; the plain stream is remembered
        until ``reset_stream_framing`` only once it delivered a document, so a
        gateway that was merely failing is offered line framing again next
        time. Timeouts and connection errors never switch the framing.

        Closing the generator (``aclose``) releases the HTTP response at once.
        """
        if not self._line_framing_rejected:
            try:
                async with aclosing(self._async_stream_documents(True)) as documents:
                    async for document in documents:
                        yield document
                return
            except _LineFramingRefused:
                pass
            async with aclosing(self._async_stream_documents(False)) as documents:
                async for document in documents:
                    # Plain works where line framing did not: remember it.
                    self._line_framing_rejected = True
                    yield document
            return
        async with aclosing(self._async_stream_documents(False)) as documents:
            async for document in documents:
                yield document

    async def _async_stream_documents(
        self, line_framing: bool
    ) -> AsyncGenerator[JsonObject]:
        """Open one stream request and yield its documents."""
        splitter = JsonDocumentSplitter()
        url = f"{self._base_url}{_STREAM_PATH}"
        if line_framing:
            url = f"{url}?{_LINE_FRAMING_QUERY}"
        _LOGGER.debug(
            "OPUS event stream requested with %s framing",
            "line" if line_framing else "plain",
        )
        headers = await self._async_auth_headers()
        tls = self._tls
        try:
            async with self._session.get(
                url,
                headers=headers,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(
                    total=None,
                    connect=CONNECT_TIMEOUT,
                    sock_connect=CONNECT_TIMEOUT,
                    sock_read=STREAM_IDLE_TIMEOUT,
                ),
                ssl=tls,
            ) as response:
                self._check_auth(response)
                if (
                    line_framing
                    and response.status != 200
                    and response.status not in _NO_FALLBACK_STATUSES
                ):
                    _LOGGER.debug(
                        "OPUS gateway answered the line-framed stream with "
                        "HTTP %s; trying the plain event stream",
                        response.status,
                    )
                    raise _LineFramingRefused
                if response.status != 200:
                    raise OpusResponseError(
                        f"status {response.status}",
                        response.status,
                        http_status=response.status,
                    )
                self._stream_framing = "line" if line_framing else "plain"
                async for chunk in response.content.iter_any():
                    for text in splitter.feed(chunk):
                        document = as_object(parse_json(text))
                        if document is not None:
                            yield document
                splitter.finish()
        except TimeoutError as err:
            raise OpusTimeoutError("The event stream went silent") from err
        except aiohttp.ServerFingerprintMismatch as err:
            raise OpusCertificateError("The gateway certificate changed") from err
        except aiohttp.ClientError as err:
            raise OpusConnectionError(f"The event stream failed: {err}") from err
        except ValueError as err:
            raise OpusResponseError("The event stream contains invalid JSON") from err


async def _read_bounded_text(response: aiohttp.ClientResponse) -> str:
    """Read a response body, refusing more than ``MAX_RESPONSE_SIZE`` bytes."""
    declared = response.content_length
    if declared is not None and declared > MAX_RESPONSE_SIZE:
        raise OpusResponseError("The gateway response is too large")
    body = bytearray()
    async for chunk in response.content.iter_chunked(_READ_CHUNK_SIZE):
        body.extend(chunk)
        if len(body) > MAX_RESPONSE_SIZE:
            raise OpusResponseError("The gateway response is too large")
    return body.decode("utf-8", errors="replace")


def _raise_for_auth(response: aiohttp.ClientResponse) -> None:
    if response.status in (401, 403):
        raise OpusAuthError(f"status {response.status}")


def _pinned_fingerprint(fingerprint: str | None) -> aiohttp.Fingerprint | None:
    """Return the pin for a SHA-256 fingerprint in hexadecimal text, if valid."""
    if not isinstance(fingerprint, str):
        return None
    try:
        digest = bytes.fromhex(fingerprint.replace(":", "").strip())
        return aiohttp.Fingerprint(digest)
    except ValueError:
        # Not hexadecimal or not a SHA-256 length: treated as no pin at all.
        return None


def _tls_probe_context() -> ssl.SSLContext:
    """Return a context that reads a certificate without checking it."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


async def async_read_certificate_fingerprint(host: str, port: int) -> str:
    """Return the SHA-256 fingerprint of the certificate a gateway presents.

    Nothing is sent: the TLS handshake is made and closed again. The result is
    only as trustworthy as the first contact with the gateway; later contacts
    are pinned to it.
    """
    literal = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        async with asyncio.timeout(CONNECT_TIMEOUT):
            _, writer = await asyncio.open_connection(
                literal, port, ssl=_tls_probe_context(), server_hostname=""
            )
    except (OSError, ssl.SSLError, TimeoutError) as err:
        raise OpusConnectionError(f"TLS handshake with {host} failed") from err
    try:
        ssl_object = writer.get_extra_info("ssl_object")
        certificate = ssl_object.getpeercert(binary_form=True) if ssl_object else None
    finally:
        writer.close()
    if not certificate:
        raise OpusResponseError("The gateway presented no certificate")
    return hashlib.sha256(certificate).hexdigest()


async def async_probe_webui_2026(
    session: aiohttp.ClientSession, host: str, port: int
) -> bool:
    """Return whether a WebUI 2026 gateway answers at ``host``:``port``.

    ``GET /auth/setup-status`` needs no login and carries no credentials. The
    certificate is not checked here (it is not known yet); a successful answer
    only tells the generation apart from other devices.
    """
    url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    try:
        async with session.get(
            f"https://{url_host}:{port}{_SETUP_STATUS_PATH}",
            allow_redirects=False,
            ssl=False,
            timeout=aiohttp.ClientTimeout(
                total=REQUEST_TIMEOUT, connect=CONNECT_TIMEOUT
            ),
        ) as response:
            if response.status != 200:
                return False
            text = await _read_bounded_text(response)
        return as_object(parse_json(text)) is not None
    except TimeoutError, aiohttp.ClientError, OpusApiError, ValueError:
        return False
