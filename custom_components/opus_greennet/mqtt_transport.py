"""Bounded MQTT request/response operations for the OPUS bridge.

Base: kegelmeier v0.3.3b0 - functionally unchanged. The gateway-connection
regression reported after switching to v0.3.3b0 was NOT in this file; it was
a duplicate subscription to the same answer topic in coordinator.py's
async_setup() racing against the request-scoped subscription this module
opens in MQTTRequestManager.async_request() (see coordinator.py module
docstring for the full root-cause analysis and fix).

Only change in this revision: added debug logging around the subscribe/
SUBACK/publish/response sequence, so a future handshake deadlock is visible
in the log immediately instead of only surfacing as an opaque 10s timeout.

FIX 2026-09-26: async_probe_gateway() now uses the /uptime endpoint instead
of /info.  The OPUS-IQ-DOT gateway (firmware v1.21.30 and earlier) does NOT
respond to get/config/system/info; it only implements get/config/system/uptime.
Using /info caused a guaranteed 10-second REQUEST_TIMEOUT warning on every
integration start, followed by the coordinator silently disabling all future
system-info probes.  Switching to /uptime eliminates the timeout entirely and
also validates the gateway response via require_status=True (httpStatus 200).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from homeassistant.components import mqtt
from homeassistant.components.mqtt import ReceiveMessage
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .const import (
    DOMAIN,
    TOPIC_BASE,
    TOPIC_GET_ANSWER_SYSTEM_UPTIME,
    TOPIC_GET_SYSTEM_UPTIME,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10.0


def request_error(key: str, device_id: str, reason: str = "") -> HomeAssistantError:
    """Create a translated transport error without logging sensitive payloads."""
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders={"device_id": device_id, "reason": reason},
    )


def decode_response(payload: str | bytes, *, require_status: bool = False) -> dict:
    """Validate an OPUS object and reject a negative protocol acknowledgement."""
    try:
        data = json.loads(payload)
    except (ValueError, TypeError, UnicodeDecodeError) as err:
        raise ValueError("The gateway returned invalid JSON") from err
    if not isinstance(data, dict) or not data:
        raise ValueError("The gateway returned an invalid response object")
    header = data.get("header")
    if header is not None or require_status:
        if not isinstance(header, dict):
            raise ValueError("The gateway response is missing its status")
        status = header.get("httpStatus")
        if isinstance(status, bool) or (
            isinstance(status, float) and not status.is_integer()
        ):
            raise ValueError("The gateway returned an invalid status")
        try:
            status = int(status)
        except (TypeError, ValueError, OverflowError) as err:
            raise ValueError("The gateway returned an invalid status") from err
        if status not in (200, 201):
            raise ValueError(f"The gateway returned status {status}")
    return data


async def async_wait_for_subscriptions(hass: HomeAssistant, topics: list[str]) -> None:
    """Wait for broker subscription completion before publishing a request."""
    pending = set(topics)
    ready = asyncio.get_running_loop().create_future()
    cancellations: list[Callable[[], None]] = []

    @callback
    def subscription_done(topic: str) -> None:
        pending.discard(topic)
        _LOGGER.debug("SUBACK received for %s (%d topic(s) still pending)", topic, len(pending))
        if not pending and not ready.done():
            ready.set_result(None)

    try:
        for topic in pending.copy():
            cancellations.append(
                mqtt.async_on_subscribe_done(
                    hass, topic, 1, lambda topic=topic: subscription_done(topic)
                )
            )
        if pending:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                await ready
    except TimeoutError:
        _LOGGER.warning(
            "Timed out waiting for MQTT SUBACK on: %s. If one of these topics "
            "is already subscribed elsewhere in the integration, Home "
            "Assistant's MQTT client may not re-fire its SUBACK callback - "
            "do not add a second standing subscription to a topic that is "
            "also used as a request/response answer topic.",
            sorted(pending),
        )
        raise
    finally:
        for cancel in cancellations:
            cancel()


class MQTTRequestManager:
    """Own subscriptions and serialize requests without protocol request IDs."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._closed = False
        self._generation = 0
        self._locks: dict[str, asyncio.Lock] = {}
        self._waiters: dict[asyncio.Future, str] = {}
        self._cleanups: set[Callable[[], None]] = set()

    def _own_cleanup(self, cleanup: Callable[[], None]) -> Callable[[], None]:
        """Make cleanup idempotent and immediately available to unload."""

        @callback
        def cancel() -> None:
            if cancel in self._cleanups:
                self._cleanups.remove(cancel)
                cleanup()

        self._cleanups.add(cancel)
        return cancel

    @callback
    def async_cancel_pending(self, key: str = "request_cancelled") -> None:
        """Fail active requests and immediately remove temporary subscriptions."""
        self._generation += 1
        for future, device_id in self._waiters.copy().items():
            if not future.done():
                future.set_exception(request_error(key, device_id))
        for cancel in list(self._cleanups):
            cancel()

    @callback
    def async_close(self) -> None:
        """Prevent new operations and cancel current response waits."""
        self._closed = True
        self.async_cancel_pending()

    async def async_request(
        self,
        topic: str,
        answer_topic: str,
        device_id: str,
        payload: str = "",
        *,
        require_status: bool = False,
        is_available: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Subscribe, await SUBACK, publish, and validate one fresh response.

        IMPORTANT: `answer_topic` must not already have a standing
        subscription elsewhere in the integration. Home Assistant's MQTT
        client does not reliably re-fire async_on_subscribe_done() for a
        topic that is already subscribed, which would make the `await
        subscribed` below hang until REQUEST_TIMEOUT on every call. If a
        topic needs both a permanent listener and to be used here, route
        the permanent listener's data through this method's response
        instead of subscribing twice.
        """
        lock = self._locks.setdefault(answer_topic, asyncio.Lock())
        generation = self._generation
        async with lock:
            if self._closed or generation != self._generation:
                raise request_error("request_cancelled", device_id)
            if not mqtt.is_connected(self.hass):
                raise request_error("mqtt_unavailable", device_id)
            if is_available is not None and not is_available():
                raise request_error("gateway_unavailable", device_id)

            loop = asyncio.get_running_loop()
            subscribed = loop.create_future()
            response = loop.create_future()
            self._waiters[subscribed] = device_id
            self._waiters[response] = device_id
            sent = False
            cancellations: list[Callable[[], None]] = []

            @callback
            def handle_response(msg: ReceiveMessage) -> None:
                if not sent or response.done() or getattr(msg, "retain", False):
                    return
                try:
                    data = decode_response(msg.payload, require_status=require_status)
                except ValueError as err:
                    response.set_exception(
                        request_error("request_rejected", device_id, str(err))
                    )
                else:
                    response.set_result(data)

            @callback
            def subscription_done() -> None:
                if not subscribed.done():
                    subscribed.set_result(None)

            try:
                async with asyncio.timeout(REQUEST_TIMEOUT):
                    _LOGGER.debug(
                        "Subscribing to answer topic %s for device %s",
                        answer_topic, device_id,
                    )
                    cancellations.append(
                        self._own_cleanup(
                            await mqtt.async_subscribe(
                                self.hass, answer_topic, handle_response, qos=1
                            )
                        )
                    )
                    cancellations.append(
                        self._own_cleanup(
                            mqtt.async_on_subscribe_done(
                                self.hass, answer_topic, 1, subscription_done
                            )
                        )
                    )
                    await subscribed
                    _LOGGER.debug(
                        "SUBACK received for %s - publishing request to %s",
                        answer_topic, topic,
                    )
                    if self._closed or generation != self._generation:
                        raise request_error("request_cancelled", device_id)
                    if not mqtt.is_connected(self.hass):
                        raise request_error("mqtt_unavailable", device_id)
                    if is_available is not None and not is_available():
                        raise request_error("gateway_unavailable", device_id)
                    sent = True
                    await mqtt.async_publish(
                        self.hass, topic, payload, qos=1, retain=False
                    )
                    result = await response
                    _LOGGER.debug("Response received on %s", answer_topic)
                    return result
            except TimeoutError as err:
                _LOGGER.warning(
                    "OPUS request timed out for device %s (topic=%s, answer=%s, "
                    "subscribed=%s). If `subscribed` is False, this topic is "
                    "likely already subscribed elsewhere in the integration "
                    "and never received a SUBACK for this request.",
                    device_id, topic, answer_topic, subscribed.done(),
                )
                raise request_error("request_timeout", device_id) from err
            finally:
                for cancel in cancellations:
                    cancel()
                for future in (subscribed, response):
                    self._waiters.pop(future, None)
                    if future.done() and not future.cancelled():
                        future.exception()
                    elif not future.done():
                        future.cancel()


async def async_probe_gateway(hass: HomeAssistant, eag_id: str) -> dict[str, Any]:
    """Verify the selected gateway responds via the /uptime endpoint.

    The OPUS-IQ-DOT gateway does NOT implement get/config/system/info.
    Using /uptime instead eliminates the guaranteed 10-second timeout that
    occurred on every integration start when /info was used.

    Expected response structure:
        {
            "header": {"httpStatus": 200, "content": "Uptime", ...},
            "systemUptimeResponse": {"uptime": <seconds_since_boot>}
        }
    """
    manager = MQTTRequestManager(hass)
    try:
        return await manager.async_request(
            TOPIC_GET_SYSTEM_UPTIME.format(base=TOPIC_BASE, eag_id=eag_id),
            TOPIC_GET_ANSWER_SYSTEM_UPTIME.format(base=TOPIC_BASE, eag_id=eag_id),
            eag_id,
            require_status=True,
        )
    except HomeAssistantError as err:
        if err.translation_key == "mqtt_unavailable":
            raise
        raise request_error("gateway_unavailable", eag_id) from err
    finally:
        manager.async_close()
