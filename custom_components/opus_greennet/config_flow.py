"""Config flow for Opus GreenNet Bridge integration."""

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

_LOGGER = logging.getLogger(__name__)

# Regex pattern for EAG ID (8 hex characters)
EAG_ID_PATTERN = re.compile(r"[0-9A-Fa-f]{8}")

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EAG_ID): str,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
    eag_id = data[CONF_EAG_ID].upper()

    # Validate EAG ID format
    if not EAG_ID_PATTERN.fullmatch(eag_id):
        raise InvalidEagId

    # Check if MQTT is available
    if not mqtt.is_connected(hass):
        raise CannotConnect

    # Return info that you want to store in the config entry.
    return {"title": f"Opus GreenNet ({eag_id})", "eag_id": eag_id}


class OpusGreenNetConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Opus GreenNet Bridge."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        # Check if MQTT integration is available
        if not await mqtt.async_wait_for_mqtt_client(self.hass):
            return self.async_abort(reason="mqtt_not_configured")

        if user_input is not None:
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
                # Check if already configured
                await self.async_set_unique_id(info["eag_id"])
                self._abort_if_unique_id_configured()

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
