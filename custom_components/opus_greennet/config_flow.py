"""Config flow for Opus GreenNet Bridge integration.

Base: kegelmeier v0.3.3b0.

FIX (this revision): validate_input() previously required
async_probe_gateway() (a request for get/config/system/info) to succeed
before the config entry could even be created. This duplicated - separately
from and inconsistently with - the coordinator's own gateway probe, which
was already made best-effort (non-fatal) because some Mosquitto bridge
configurations legitimately never relay this diagnostic-only endpoint (see
coordinator.py's ISSUE_SYSTEM_INFO_UNSUPPORTED handling). The config flow
was never updated to match, so re-adding a gateway with exactly this bridge
setup failed at the very first step with "gateway_unavailable", even though
the coordinator would have set up and worked correctly.

Now: the config flow still confirms MQTT itself is connected (a real,
blocking prerequisite - if the HA MQTT integration isn't set up at all,
nothing can ever work), but a failed/timed-out system-info probe during
setup is now only logged as a warning, not fatal. The actual gateway
health/availability - based on subscriptions and device discovery, not this
one optional diagnostic topic - is still verified afterwards by the
coordinator during async_setup_entry(), which correctly fails
ConfigEntryNotReady if the gateway is truly unreachable (e.g. MQTT bridge
down entirely, wrong EAG ID with no devices ever reporting).
"""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import CONF_EAG_ID, DOMAIN
from .mqtt_transport import async_probe_gateway

_LOGGER = logging.getLogger(__name__)

EAG_ID_PATTERN = re.compile(r"[0-9A-Fa-f]{8}")

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EAG_ID): str,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the
    user.
    """
    eag_id = data[CONF_EAG_ID].strip().upper()

    if not EAG_ID_PATTERN.fullmatch(eag_id):
        raise InvalidEagId

    if not mqtt.is_connected(hass):
        raise CannotConnect

    try:
        await async_probe_gateway(hass, eag_id)
    except HomeAssistantError as err:
        # FIX: no longer fatal. get/config/system/info is diagnostic-only
        # and some Mosquitto bridge configurations never relay it - the
        # coordinator's own setup (which uses get/devices, the topic that
        # actually matters for functionality) still validates real gateway
        # reachability right after this flow completes.
        _LOGGER.warning(
            "OPUS gateway %s did not answer the setup-time system-info probe "
            "(%s). Continuing anyway - check your Mosquitto bridge if the "
            "gateway does not become available after setup. Device control "
            "and discovery do not depend on this probe.",
            eag_id,
            err,
        )

    return {"title": f"Opus GreenNet ({eag_id})", "eag_id": eag_id}


class OpusGreenNetConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Opus GreenNet Bridge."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if not await mqtt.async_wait_for_mqtt_client(self.hass):
            return self.async_abort(reason="mqtt_not_configured")

        if user_input is not None:
            eag_id = user_input[CONF_EAG_ID].strip().upper()
            if EAG_ID_PATTERN.fullmatch(eag_id):
                await self.async_set_unique_id(eag_id)
                self._abort_if_unique_id_configured()
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidEagId:
                errors[CONF_EAG_ID] = "invalid_eag_id"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=info["title"],
                    data={CONF_EAG_ID: info["eag_id"]},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidEagId(HomeAssistantError):
    """Error to indicate the EAG ID is invalid."""
