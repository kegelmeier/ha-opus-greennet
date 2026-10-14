"""The Opus GreenNet Bridge integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import probatio as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import (
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.typing import ConfigType

from .const import CONF_EAG_ID, DOMAIN
from .coordinator import OpusGreenNetCoordinator

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.LIGHT,
    Platform.SWITCH,
    Platform.COVER,
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.EVENT,
    Platform.LOCK,
]

SERVICE_GET_DEVICE_CONFIG = "get_device_configuration"
SERVICE_SET_DEVICE_CONFIG = "set_device_configuration"
SERVICE_GET_DEVICE_PARAMS = "get_device_parameters"
SERVICE_RELOAD_ENTRY = "reload_entry"
ATTR_DEVICE_ID = "device_id"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_CONFIGURATION = "configuration"

SERVICE_DEVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)

SERVICE_SET_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_CONFIGURATION): dict,
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)


@dataclass(slots=True)
class OpusGreenNetRuntimeData:
    """Runtime objects owned by one config entry."""

    coordinator: OpusGreenNetCoordinator
    gateway_device_id: str


type OpusGreenNetConfigEntry = ConfigEntry[OpusGreenNetRuntimeData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration-wide service actions."""
    _register_services(hass)
    return True


def _loaded_entry(
    hass: HomeAssistant, config_entry_id: str | None
) -> OpusGreenNetConfigEntry:
    """Resolve exactly one loaded Opus GreenNet config entry."""
    if config_entry_id:
        entry = hass.config_entries.async_get_entry(config_entry_id)
        if entry is None or entry.domain != DOMAIN:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="config_entry_not_found",
                translation_placeholders={"config_entry_id": config_entry_id},
            )
        if entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="config_entry_not_loaded",
                translation_placeholders={"config_entry_id": config_entry_id},
            )
        return entry

    loaded_entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not loaded_entries:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="no_loaded_config_entry",
        )
    if len(loaded_entries) > 1:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="config_entry_required",
        )
    return loaded_entries[0]


def _coordinator_for_call(
    hass: HomeAssistant, call: ServiceCall
) -> OpusGreenNetCoordinator:
    """Return the coordinator selected by a service call."""
    return _loaded_entry(
        hass, call.data.get(ATTR_CONFIG_ENTRY_ID)
    ).runtime_data.coordinator


def _validate_service_device(
    coordinator: OpusGreenNetCoordinator, device_id: str
) -> None:
    """Validate that a requested device belongs to the selected gateway."""
    if coordinator.get_device(device_id) is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="device_not_found",
            translation_placeholders={"device_id": device_id},
        )


def _describe_setup_failure(err: Exception) -> str:
    """Return a short, payload-free description of why gateway setup failed."""
    if isinstance(err, HomeAssistantError) and err.translation_key:
        reason = (err.translation_placeholders or {}).get("reason")
        return f"{err.translation_key} ({reason})" if reason else err.translation_key
    return f"{type(err).__name__}: {err}" if str(err) else type(err).__name__


async def async_setup_entry(
    hass: HomeAssistant, entry: OpusGreenNetConfigEntry
) -> bool:
    """Set up Opus GreenNet Bridge from a config entry."""
    eag_id = entry.data[CONF_EAG_ID]
    coordinator = OpusGreenNetCoordinator(hass, eag_id)

    try:
        await coordinator.async_setup()
    except (HomeAssistantError, OSError) as err:
        await coordinator.async_unload()
        # The translated ConfigEntryNotReady text is deliberately generic.
        # Record the precise cause so request_timeout, mqtt_unavailable and
        # an unreachable gateway can be told apart in the log.
        _LOGGER.warning(
            "OPUS gateway %s is not ready, Home Assistant will retry: %s",
            eag_id,
            _describe_setup_failure(err),
        )
        _LOGGER.debug("Setup failure details for %s", eag_id, exc_info=err)
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="gateway_unavailable",
        ) from err
    except BaseException:
        await coordinator.async_unload()
        raise

    try:
        device_registry = dr.async_get(hass)
        gateway_device = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, eag_id)},
            manufacturer="OPUS",
            model="GreenNet Bridge",
            name=entry.title,
            serial_number=eag_id,
        )
        entry.runtime_data = OpusGreenNetRuntimeData(
            coordinator=coordinator,
            gateway_device_id=gateway_device.id,
        )
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await coordinator.async_unload()
        raise

    _LOGGER.info("Opus GreenNet Bridge setup complete: %s", eag_id)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: OpusGreenNetConfigEntry
) -> bool:
    """Unload a config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    await entry.runtime_data.coordinator.async_unload()
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: OpusGreenNetConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Forget one child locally; never unpair it or remove the gateway."""
    eag_id = entry.data[CONF_EAG_ID]
    if device_entry.config_entry_id != entry.entry_id:
        return False
    if (DOMAIN, eag_id) in device_entry.identifiers:
        return False
    prefix = f"{eag_id}_"
    child_ids = [
        identifier[len(prefix) :]
        for domain, identifier in device_entry.identifiers
        if domain == DOMAIN
        and identifier.startswith(prefix)
        and identifier[len(prefix) :]
    ]
    if len(child_ids) != 1:
        return False
    # Registry-only stale devices can also be removed when this entry is unloaded.
    if entry.state is ConfigEntryState.LOADED:
        entry.runtime_data.coordinator.async_forget_device(child_ids[0])
    return True


def _register_services(hass: HomeAssistant) -> None:
    """Register integration service actions once."""
    if hass.services.has_service(DOMAIN, SERVICE_GET_DEVICE_CONFIG):
        return

    async def handle_get_device_configuration(
        call: ServiceCall,
    ) -> ServiceResponse:
        coordinator = _coordinator_for_call(hass, call)
        device_id = call.data[ATTR_DEVICE_ID]
        _validate_service_device(coordinator, device_id)
        result = await coordinator.async_get_device_configuration(device_id)
        if result is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="device_request_timeout",
                translation_placeholders={"device_id": device_id},
            )
        return result

    async def handle_set_device_configuration(call: ServiceCall) -> None:
        coordinator = _coordinator_for_call(hass, call)
        device_id = call.data[ATTR_DEVICE_ID]
        _validate_service_device(coordinator, device_id)
        await coordinator.async_set_device_configuration(
            device_id, call.data[ATTR_CONFIGURATION]
        )

    async def handle_get_device_parameters(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator_for_call(hass, call)
        device_id = call.data[ATTR_DEVICE_ID]
        _validate_service_device(coordinator, device_id)
        result = await coordinator.async_get_device_parameters(device_id)
        if result is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="device_request_timeout",
                translation_placeholders={"device_id": device_id},
            )
        return result

    async def handle_reload_entry(call: ServiceCall) -> None:
        config_entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
        if config_entry_id:
            entry = _loaded_entry(hass, config_entry_id)
            await hass.config_entries.async_reload(entry.entry_id)
            return

        for loaded_entry in hass.config_entries.async_loaded_entries(DOMAIN):
            await hass.config_entries.async_reload(loaded_entry.entry_id)

    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_GET_DEVICE_CONFIG,
        handle_get_device_configuration,
        schema=SERVICE_DEVICE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_SET_DEVICE_CONFIG,
        handle_set_device_configuration,
        schema=SERVICE_SET_CONFIG_SCHEMA,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_GET_DEVICE_PARAMS,
        handle_get_device_parameters,
        schema=SERVICE_DEVICE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_RELOAD_ENTRY,
        handle_reload_entry,
        schema=vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string}),
    )
