"""The Opus GreenNet Bridge integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import probatio as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .api import (
    GENERATION_LEGACY,
    OpusApiError,
    OpusAuthError,
    OpusConnectionError,
    OpusGatewayClient,
)
from .const import (
    CONF_CERTIFICATE_FINGERPRINT,
    CONF_EAG_ID,
    CONF_GENERATION,
    DEFAULT_PORT,
    DEFAULT_USERNAME,
    DOMAIN,
    REPOSITIONING_DIRECT,
    REPOSITIONING_FIRST_DOWN,
    REPOSITIONING_FIRST_UP,
    SETTING_GUARD_STORE_KEY,
    SETTING_GUARD_STORE_VERSION,
    WEBUI_2026_PORT,
)
from .coordinator import GatewayMismatchError, OpusGreenNetCoordinator
from .discovery import (
    async_detect_webui_2026,
    async_local_networks,
    async_locate_gateway,
    default_connection,
    is_local_address,
    parse_host_literal,
)
from .entity import (
    async_delete_config_entry_issues,
    async_set_non_local_address_issue,
)
from .wire import json_body_problem
from .write_access import async_setup_write_access_notice, setting_writes_allowed

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.LIGHT,
    Platform.SWITCH,
    Platform.COVER,
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.EVENT,
    Platform.LOCK,
    Platform.NUMBER,
    Platform.SELECT,
]

SERVICE_GET_DEVICE_CONFIG = "get_device_configuration"
SERVICE_SET_DEVICE_CONFIG = "set_device_configuration"
SERVICE_GET_DEVICE_PARAMS = "get_device_parameters"
SERVICE_GET_DEVICE_LINK_TABLES = "get_device_link_tables"
SERVICE_RELOAD_ENTRY = "reload_entry"
SERVICE_SET_COVER_POSITION = "set_cover_position"
ATTR_DEVICE_ID = "device_id"
ATTR_POSITION = "position"
ATTR_REPOSITIONING = "repositioning"
ATTR_CHANNEL = "channel"
# Service values (snake case for the selector translations) -> wire values.
REPOSITIONING_OPTIONS: dict[str, str] = {
    "direct": REPOSITIONING_DIRECT,
    "first_up": REPOSITIONING_FIRST_UP,
    "first_down": REPOSITIONING_FIRST_DOWN,
}
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


SERVICE_SET_COVER_POSITION_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_POSITION): vol.All(vol.Coerce(int), vol.Range(0, 100)),
        vol.Optional(ATTR_REPOSITIONING, default="direct"): vol.In(
            REPOSITIONING_OPTIONS
        ),
        vol.Optional(ATTR_CHANNEL, default=0): vol.All(
            vol.Coerce(int), vol.Range(min=0)
        ),
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


async def _async_locate_legacy_gateway(
    hass: HomeAssistant, entry: OpusGreenNetConfigEntry
) -> None:
    """Store the address of a former MQTT entry's gateway if it can be found.

    A gateway that only rejected the default credentials is stored as well, so
    the reauthentication form shows its address and only asks for the password.
    """
    eag_id = entry.data[CONF_EAG_ID]
    located = await async_locate_gateway(hass, eag_id)
    if located is None:
        _LOGGER.info("OPUS gateway %s was not found in the local network", eag_id)
        return
    if located.verified:
        _LOGGER.info("OPUS gateway %s found in the local network", eag_id)
    else:
        _LOGGER.info(
            "OPUS gateway %s probably found in the local network; it rejected "
            "the default credentials",
            eag_id,
        )
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, **default_connection(located.host, eag_id)}
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: OpusGreenNetConfigEntry
) -> bool:
    """Set up Opus GreenNet Bridge from a config entry."""
    eag_id = entry.data[CONF_EAG_ID]
    if CONF_HOST not in entry.data:
        # Entries created for the former MQTT transport only know the EAG
        # identifier. Look for the gateway in the local network first; the
        # reauthentication flow asks for the address only if it is not found.
        await _async_locate_legacy_gateway(hass, entry)
    if CONF_HOST not in entry.data:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="gateway_address_required"
        )
    configured = parse_host_literal(entry.data[CONF_HOST])
    # An entry without a generation is a gateway of the earlier generation.
    generation = entry.data.get(CONF_GENERATION, GENERATION_LEGACY)
    # Only that generation sends its credentials unencrypted.
    non_local = (
        generation == GENERATION_LEGACY
        and configured is not None
        and not is_local_address(configured, await async_local_networks(hass))
    )
    async_set_non_local_address_issue(hass, entry.entry_id, non_local=non_local)
    if non_local:
        # Existing entries keep working. The gateway speaks unencrypted HTTP,
        # so credentials sent to a non-local address can be read on the way.
        _LOGGER.warning(
            "The configured OPUS gateway address is not in a local network; "
            "the gateway has no TLS. Use a VPN or a private address instead"
        )
    if generation == GENERATION_LEGACY:
        client = OpusGatewayClient(
            async_get_clientsession(hass),
            entry.data[CONF_HOST],
            entry.data.get(CONF_PORT, DEFAULT_PORT),
            entry.data.get(CONF_USERNAME, DEFAULT_USERNAME),
            entry.data[CONF_PASSWORD],
        )
    else:
        client = OpusGatewayClient(
            async_get_clientsession(hass),
            entry.data[CONF_HOST],
            entry.data.get(CONF_PORT, WEBUI_2026_PORT),
            entry.data.get(CONF_USERNAME, DEFAULT_USERNAME),
            entry.data[CONF_PASSWORD],
            generation=generation,
            fingerprint=entry.data.get(CONF_CERTIFICATE_FINGERPRINT),
        )

    async def _newer_generation_answers() -> bool:
        return (
            await async_detect_webui_2026(
                hass, entry.data[CONF_HOST], entry.data.get(CONF_PORT, DEFAULT_PORT)
            )
            is not None
        )

    coordinator = OpusGreenNetCoordinator(
        hass,
        client,
        eag_id,
        on_auth_failed=lambda: entry.async_start_reauth(hass),
        generation_check=(
            _newer_generation_answers if generation == GENERATION_LEGACY else None
        ),
        write_access_allowed=lambda: setting_writes_allowed(entry),
    )

    try:
        await coordinator.async_restore_setting_guards(
            _setting_guard_store(hass, eag_id)
        )
        await coordinator.async_setup()
    except OpusAuthError as err:
        await coordinator.async_unload()
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="invalid_auth"
        ) from err
    except GatewayMismatchError as err:
        await coordinator.async_unload()
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="wrong_gateway"
        ) from err
    except OpusApiError as err:
        await coordinator.async_unload()
        if (
            generation == GENERATION_LEGACY
            and isinstance(err, OpusConnectionError)
            and await async_detect_webui_2026(
                hass, entry.data[CONF_HOST], entry.data.get(CONF_PORT, DEFAULT_PORT)
            )
            is not None
        ):
            # The gateway no longer answers on its old port but a WebUI 2026
            # gateway does: it was updated. It needs a user login, which the
            # reauthentication asks for. Nothing is changed before that.
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="webui_2026_login_required",
            ) from err
        # The translated ConfigEntryNotReady text is deliberately generic;
        # the log keeps the precise cause.
        _LOGGER.warning(
            "OPUS gateway %s is not ready, Home Assistant will retry: %s",
            eag_id,
            err,
        )
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="gateway_unavailable",
        ) from err
    except BaseException:
        await coordinator.async_unload()
        raise

    try:
        version = coordinator.gateway_info.get("version")
        serial = coordinator.gateway_info.get("serialNumber")
        device_registry = dr.async_get(hass)
        gateway_device = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, eag_id)},
            manufacturer="OPUS",
            model="GreenNet Bridge",
            name=entry.title,
            serial_number=serial if isinstance(serial, str) and serial else None,
            sw_version=version if isinstance(version, str) else None,
        )
        entry.runtime_data = OpusGreenNetRuntimeData(
            coordinator=coordinator,
            gateway_device_id=gateway_device.id,
        )
        _remove_daily_read_entities(hass, eag_id)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        async_setup_write_access_notice(hass, entry)
    except BaseException:
        await coordinator.async_unload()
        raise

    _LOGGER.info("Opus GreenNet Bridge setup complete: %s", eag_id)
    return True


# Entities of the daily settings read of v1.2.0b1, which was removed again.
_REMOVED_DAILY_READ_ENTITIES: tuple[tuple[str, str], ...] = (
    ("switch", "daily_settings_read"),
    ("time", "daily_settings_read_time"),
    ("sensor", "daily_settings_run"),
)


def _remove_daily_read_entities(hass: HomeAssistant, eag_id: str) -> None:
    """Remove the entities of the daily settings read left by v1.2.0b1."""
    registry = er.async_get(hass)
    for platform, suffix in _REMOVED_DAILY_READ_ENTITIES:
        if entity_id := registry.async_get_entity_id(
            platform, DOMAIN, f"{eag_id}_{suffix}"
        ):
            registry.async_remove(entity_id)


def _setting_guard_store(hass: HomeAssistant, eag_id: str) -> Store[dict[str, Any]]:
    """Return where the protection of confirmed settings of a gateway is saved."""
    return Store(
        hass, SETTING_GUARD_STORE_VERSION, f"{SETTING_GUARD_STORE_KEY}.{eag_id}"
    )


async def async_unload_entry(
    hass: HomeAssistant, entry: OpusGreenNetConfigEntry
) -> bool:
    """Unload a config entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    await entry.runtime_data.coordinator.async_unload()
    return True


async def async_remove_entry(
    hass: HomeAssistant, entry: OpusGreenNetConfigEntry
) -> None:
    """Remove repair issues and the saved bookkeeping of the removed entry."""
    async_delete_config_entry_issues(hass, entry.entry_id)
    if eag_id := entry.data.get(CONF_EAG_ID):
        await _setting_guard_store(hass, eag_id).async_remove()


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
        return await coordinator.async_get_device_configuration(device_id)

    async def handle_set_device_configuration(call: ServiceCall) -> None:
        coordinator = _coordinator_for_call(hass, call)
        device_id = call.data[ATTR_DEVICE_ID]
        _validate_service_device(coordinator, device_id)
        configuration = call.data[ATTR_CONFIGURATION]
        # Radio write: refuse a body that cannot be encoded as JSON before it
        # is queued or sent.
        if (problem := json_body_problem(configuration)) is not None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_configuration",
                translation_placeholders={"device_id": device_id, "reason": problem},
            )
        await coordinator.async_set_device_configuration(device_id, configuration)

    async def handle_get_device_parameters(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator_for_call(hass, call)
        device_id = call.data[ATTR_DEVICE_ID]
        _validate_service_device(coordinator, device_id)
        return await coordinator.async_get_device_parameters(device_id)

    async def handle_get_device_link_tables(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator_for_call(hass, call)
        device_id = call.data[ATTR_DEVICE_ID]
        _validate_service_device(coordinator, device_id)
        return await coordinator.async_get_device_link_tables(device_id)

    async def handle_set_cover_position(call: ServiceCall) -> None:
        coordinator = _coordinator_for_call(hass, call)
        device_id = call.data[ATTR_DEVICE_ID]
        _validate_service_device(coordinator, device_id)
        device = coordinator.get_device(device_id)
        repositioning = REPOSITIONING_OPTIONS[call.data[ATTR_REPOSITIONING]]
        channel = call.data[ATTR_CHANNEL]
        if (
            device is None
            or not device.is_cover
            or channel >= device.channel_count
            or (
                repositioning != REPOSITIONING_DIRECT
                and not device.supports_repositioning
            )
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="repositioning_not_supported",
                translation_placeholders={"device_id": device_id},
            )
        # Home Assistant position (100 = open) -> OPUS position (0 = open).
        await coordinator.async_set_cover_position(
            device_id,
            100 - call.data[ATTR_POSITION],
            channel,
            repositioning=(
                None if repositioning == REPOSITIONING_DIRECT else repositioning
            ),
        )

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
        SERVICE_GET_DEVICE_LINK_TABLES,
        handle_get_device_link_tables,
        schema=SERVICE_DEVICE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_COVER_POSITION,
        handle_set_cover_position,
        schema=SERVICE_SET_COVER_POSITION_SCHEMA,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_RELOAD_ENTRY,
        handle_reload_entry,
        schema=vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string}),
    )
