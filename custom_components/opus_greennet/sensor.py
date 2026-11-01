"""Sensor platform for Opus GreenNet Bridge integration."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Final

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    LIGHT_LUX,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfPower,
    UnitOfRatio,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import OpusGreenNetConfigEntry
from .const import (
    CONF_EAG_ID,
    DEFAULT_CHANNEL,
    DOMAIN,
    SIGNAL_GOOD_DBM,
    SIGNAL_POOR_DBM,
)
from .coordinator import (
    SIGNAL_AVAILABILITY_UPDATE,
    SIGNAL_DEVICE_DISCOVERED,
    SIGNAL_DEVICE_REMOVED,
    SIGNAL_DEVICE_STATE_UPDATE,
    SIGNAL_GATEWAY_INFO_UPDATE,
    SIGNAL_GATEWAY_UPTIME_UPDATE,
    CoverRun,
    OpusGreenNetCoordinator,
)
from .enocean_device import EnOceanChannel, EnOceanDevice
from .entity import OpusGreenNetEntity, migrate_legacy_entity_suffix
from .number import DEVICE_TIMES, PRESENCE_SETTINGS, DeviceTime
from .select import DEVICE_SELECTS, DeviceSelect, option_slug

# Actuators hold link tables; sensors and wall switches never list one.
LINKABLE_ENTITY_TYPES: Final = frozenset({"switch", "light", "cover"})

# Sensor state is pushed by the coordinator.
PARALLEL_UPDATES = 0


# Attributes of the run time sensor, restored with its value.
RUN_ATTRIBUTES: Final = (
    "direction",
    "start_position",
    "end_position",
    "full_travel",
    "finished_at",
)

# Numeric channel readings exposed by OpusGreenNetMeasurementSensor.
MEASUREMENT_READINGS: Final[dict[str, Callable[[EnOceanChannel], float | None]]] = {
    "temperature": lambda channel: channel.temperature,
    "illuminance": lambda channel: channel.illuminance,
    "supply_voltage": lambda channel: channel.supply_voltage,
    "battery_level": lambda channel: channel.battery_level,
}

# Battery levels are shown as whole percent and the presence detector's
# illuminance as whole lux. Display only: the reported state and history are
# unchanged, and a user-chosen display precision in the entity settings takes
# precedence.
# Decimals shown for the stored settings. They stay in their native unit: a
# suggested unit would scale the precision of an entity that already has
# another unit (HA adds digits), which showed "0,500 s". A user can still pick
# minutes in the entity settings. The warning signal has 1 ms steps but is
# used with values like 0.5 s, so it shows one decimal.
SETTING_DISPLAY_PRECISION: Final[dict[str, int]] = {
    "timer": 0,
    "timer_long": 0,
    "switch_off_warning": 0,
    "switch_off_warning_signal": 1,
    "illumination_threshold": 0,
    "pir_off_time": 0,
    "recom_ready_time": 0,
    "alive_cycle": 0,
}
MEASUREMENT_DISPLAY_PRECISION: Final[dict[str, int]] = {
    "battery_level": 0,
    "illuminance": 0,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OpusGreenNetConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Opus GreenNet sensors from a config entry."""
    coordinator = entry.runtime_data.coordinator
    gateway_device_id = entry.runtime_data.gateway_device_id
    eag_id = entry.data[CONF_EAG_ID]
    added_unique_ids: set[str] = set()
    # Devices waiting for their first link table: device id -> unsubscribe.
    waiting_for_links: dict[str, CALLBACK_TYPE] = {}
    async_add_entities(
        [
            OpusGreenNetGatewayUptimeSensor(coordinator, eag_id),
            *(
                OpusGreenNetGatewayInfoSensor(coordinator, eag_id, key)
                for key in GATEWAY_INFO_READINGS
            ),
        ]
    )

    @callback
    def async_add_sensors(device: EnOceanDevice) -> None:
        """Add sensor entities for a discovered device."""
        entities: list[SensorEntity] = []

        # Climate devices get humidity, feed temperature, and energy sensors
        if device.is_climate:
            # Humidity sensor (all HeatArea types)
            entities.append(
                OpusGreenNetHumiditySensor(
                    coordinator=coordinator,
                    eag_id=eag_id,
                    gateway_device_id=gateway_device_id,
                    device=device,
                )
            )

            # Feed temperature (D1-4B-05 Valve Area only)
            if device.primary_eep == "D1-4B-05":
                entities.append(
                    OpusGreenNetFeedTemperatureSensor(
                        coordinator=coordinator,
                        eag_id=eag_id,
                        gateway_device_id=gateway_device_id,
                        device=device,
                    )
                )

            # Energy consumption (D1-4B-07 Electro Heating only)
            if device.primary_eep == "D1-4B-07":
                entities.append(
                    OpusGreenNetPowerConsumptionSensor(
                        coordinator=coordinator,
                        eag_id=eag_id,
                        gateway_device_id=gateway_device_id,
                        device=device,
                    )
                )

        if device.is_presence_detector:
            if f"{eag_id}_{device.device_id}_illuminance" not in added_unique_ids:
                migrate_legacy_entity_suffix(
                    hass,
                    entry.entry_id,
                    "sensor",
                    f"{eag_id}_{device.device_id}",
                    "illumination",
                    "illuminance",
                )
            # The SMS supply voltage is device telemetry, so it is a diagnostic
            # entity. It stays enabled; existing entries keep their settings.
            # Only A5-07-03 reports illumination (A5-07-01 has no sensor).
            readings: list[
                tuple[str, SensorDeviceClass, str, EntityCategory | None]
            ] = []
            if device.reports_illuminance:
                readings.append(
                    ("illuminance", SensorDeviceClass.ILLUMINANCE, LIGHT_LUX, None)
                )
            readings += [
                (
                    "supply_voltage",
                    SensorDeviceClass.VOLTAGE,
                    UnitOfElectricPotential.VOLT,
                    EntityCategory.DIAGNOSTIC,
                ),
                (
                    "battery_level",
                    SensorDeviceClass.BATTERY,
                    UnitOfRatio.PERCENTAGE,
                    None,
                ),
            ]
            for suffix, device_class, unit, category in readings:
                entities.append(
                    OpusGreenNetMeasurementSensor(
                        coordinator,
                        eag_id,
                        gateway_device_id,
                        device,
                        suffix,
                        device_class,
                        unit,
                        entity_category=category,
                    )
                )

        if device.is_temperature_humidity_sensor:
            entities.append(
                OpusGreenNetMeasurementSensor(
                    coordinator,
                    eag_id,
                    gateway_device_id,
                    device,
                    "temperature",
                    SensorDeviceClass.TEMPERATURE,
                    UnitOfTemperature.CELSIUS,
                )
            )
            entities.append(
                OpusGreenNetHumiditySensor(
                    coordinator=coordinator,
                    eag_id=eag_id,
                    gateway_device_id=gateway_device_id,
                    device=device,
                )
            )
            entities.append(
                OpusGreenNetMeasurementSensor(
                    coordinator,
                    eag_id,
                    gateway_device_id,
                    device,
                    "battery_level",
                    SensorDeviceClass.BATTERY,
                    UnitOfRatio.PERCENTAGE,
                )
            )

        if device.is_window_handle:
            entities.append(
                OpusGreenNetHandleSensor(
                    coordinator, eag_id, gateway_device_id, device, "handle_state"
                )
            )
            # Battery-powered handles: the device list reports the level.
            # Mains-powered actuators get no battery sensor.
            entities.append(
                OpusGreenNetMeasurementSensor(
                    coordinator,
                    eag_id,
                    gateway_device_id,
                    device,
                    "battery_level",
                    SensorDeviceClass.BATTERY,
                    UnitOfRatio.PERCENTAGE,
                    entity_category=EntityCategory.DIAGNOSTIC,
                )
            )
            if device.has_autolock:
                entities.append(
                    OpusGreenNetHandleSensor(
                        coordinator, eag_id, gateway_device_id, device, "unlock_request"
                    )
                )

        if device.is_cover:
            # Measured travel of the last command (diagnostic).
            entities.extend(
                OpusGreenNetCoverRunSensor(
                    coordinator, eag_id, gateway_device_id, device, channel_id
                )
                for channel_id in range(device.channel_count)
            )

        # Signal strength sensor (all devices with dbm data)
        entities.append(
            OpusGreenNetSignalStrengthSensor(
                coordinator=coordinator,
                eag_id=eag_id,
                gateway_device_id=gateway_device_id,
                device=device,
            )
        )

        entities.append(
            OpusGreenNetSignalQualitySensor(
                coordinator, eag_id, gateway_device_id, device
            )
        )

        # Time of the last telegram or listed update (all devices, diagnostic).
        entities.append(
            OpusGreenNetLastSeenSensor(
                coordinator=coordinator,
                eag_id=eag_id,
                gateway_device_id=gateway_device_id,
                device=device,
            )
        )

        # The device ID (EURID) as text, for every device (diagnostic).
        entities.append(
            OpusGreenNetDeviceIdSensor(coordinator, eag_id, gateway_device_id, device)
        )

        # When the settings shown below were last read from the device
        # (diagnostic), for every device that has settings.
        if any(
            device_time.applies_to(device)
            for device_time in (*DEVICE_TIMES, *PRESENCE_SETTINGS)
        ):
            entities.append(
                OpusGreenNetSettingsReadSensor(
                    coordinator, eag_id, gateway_device_id, device
                )
            )

        # The settings stored on the device, read only (diagnostic). The number
        # and select entities that write them are disabled by default.
        entities.extend(
            OpusGreenNetSettingSensor(
                coordinator, eag_id, gateway_device_id, device, device_time
            )
            for device_time in (*DEVICE_TIMES, *PRESENCE_SETTINGS)
            if device_time.applies_to(device)
        )
        entities.extend(
            OpusGreenNetSettingChoiceSensor(
                coordinator, eag_id, gateway_device_id, device, select, values
            )
            for select in DEVICE_SELECTS
            if (values := select.values_for(device)) is not None
        )

        # Wall switches linked to an actuator (diagnostic). Only devices whose
        # gateway listed a link table get one; the others have no such table.
        # A table that appears later (device list refresh) adds it then.
        if device.link_tables is not None:
            waiting_for_links.pop(device.device_id, lambda: None)()
            entities.append(
                OpusGreenNetWallSwitchLinksSensor(
                    coordinator=coordinator,
                    eag_id=eag_id,
                    gateway_device_id=gateway_device_id,
                    device=device,
                )
            )
        elif (
            device.entity_type in LINKABLE_ENTITY_TYPES
            and device.device_id not in waiting_for_links
        ):
            waiting_for_links[device.device_id] = async_dispatcher_connect(
                hass,
                f"{SIGNAL_DEVICE_STATE_UPDATE}_{eag_id}_{device.device_id}",
                async_link_table_listed,
            )

        new_entities = [
            entity for entity in entities if entity.unique_id not in added_unique_ids
        ]
        added_unique_ids.update(
            entity.unique_id for entity in new_entities if entity.unique_id is not None
        )

        if new_entities:
            async_add_entities(new_entities)

    @callback
    def async_link_table_listed(device: EnOceanDevice) -> None:
        """Add the link sensor once the gateway lists a table for the device."""
        if device.link_tables is not None:
            async_add_sensors(device)

    @callback
    def async_forget_sensors(device_id: str) -> None:
        waiting_for_links.pop(device_id, lambda: None)()
        prefix = f"{eag_id}_{device_id}_"
        added_unique_ids.difference_update(
            [
                unique_id
                for unique_id in added_unique_ids
                if unique_id.startswith(prefix)
            ]
        )

    unsubscribe_removed = async_dispatcher_connect(
        hass, f"{SIGNAL_DEVICE_REMOVED}_{eag_id}", async_forget_sensors
    )

    @callback
    def async_unload() -> None:
        unsubscribe_removed()
        for unsubscribe in waiting_for_links.values():
            unsubscribe()
        waiting_for_links.clear()

    entry.async_on_unload(async_unload)

    # Add entities for already discovered devices. This comes before the
    # discovery listener below, which stays the last subscription made here.
    for device in coordinator.devices.values():
        async_add_sensors(device)

    # Listen for new device discoveries
    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{SIGNAL_DEVICE_DISCOVERED}_{eag_id}",
            async_add_sensors,
        )
    )


class OpusGreenNetBaseSensor(OpusGreenNetEntity, SensorEntity):
    """Base class for Opus GreenNet sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
        suffix: str,
        translation_key: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, eag_id, gateway_device_id, device)
        self._attr_unique_id = f"{eag_id}_{device.device_id}_{suffix}"
        self._attr_translation_key = translation_key


class OpusGreenNetHumiditySensor(OpusGreenNetBaseSensor):
    """Humidity sensor for HeatArea devices."""

    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfRatio.PERCENTAGE

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the humidity sensor."""
        super().__init__(
            coordinator, eag_id, gateway_device_id, device, "humidity", "humidity"
        )

    @property
    def native_value(self) -> float | None:
        """Return the humidity value."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        return channel.humidity if channel else None


class OpusGreenNetFeedTemperatureSensor(OpusGreenNetBaseSensor):
    """Feed temperature sensor for Valve Area (D1-4B-05) devices."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the feed temperature sensor."""
        super().__init__(
            coordinator,
            eag_id,
            gateway_device_id,
            device,
            "feed_temperature",
            "feed_temperature",
        )

    @property
    def native_value(self) -> float | None:
        """Return the feed temperature value."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        return channel.feed_temperature if channel else None


class OpusGreenNetPowerConsumptionSensor(OpusGreenNetBaseSensor):
    """Power consumption sensor for Electro Heating Area devices."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the energy consumption sensor."""
        super().__init__(
            coordinator,
            eag_id,
            gateway_device_id,
            device,
            "energy_consumption",
            "power_consumption",
        )

    @property
    def native_value(self) -> float | None:
        """Return the energy consumption value."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        return channel.energy_consumption if channel else None


class OpusGreenNetSignalStrengthSensor(OpusGreenNetBaseSensor):
    """Signal strength sensor for all devices."""

    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the signal strength sensor."""
        super().__init__(
            coordinator,
            eag_id,
            gateway_device_id,
            device,
            "signal_strength",
            "signal_strength",
        )
        self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> int | None:
        """Return the signal strength value."""
        if self._device.dbm is not None:
            return self._device.dbm
        return None


class OpusGreenNetSignalQualitySensor(OpusGreenNetBaseSensor):
    """Radio link quality of a device, rated from its signal strength."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_options = ["good", "sufficient", "poor"]

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the signal quality sensor."""
        super().__init__(
            coordinator,
            eag_id,
            gateway_device_id,
            device,
            "signal_quality",
            "signal_quality",
        )

    @property
    def native_value(self) -> str | None:
        """Return good, sufficient or poor, or None without a signal strength."""
        dbm = self._device.dbm
        if dbm is None:
            return None
        if dbm > SIGNAL_GOOD_DBM:
            return "good"
        return "sufficient" if dbm >= SIGNAL_POOR_DBM else "poor"


def parse_last_seen(value: str) -> datetime | None:
    """Return the gateway's last-seen time, or None when it is not usable.

    The gateway writes an ISO 8601 time with a UTC offset (for example
    ``2026-10-19T18:17:17.571+0200``). A missing, malformed or offset-less
    value is unknown: a timestamp sensor must be timezone-aware, and the
    time zone is never guessed.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


class OpusGreenNetDeviceIdSensor(OpusGreenNetBaseSensor):
    """The device ID (EURID) of a device.

    A diagnostic sensor, disabled by default: the ID is already shown as the
    serial number on the device page.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the device ID sensor."""
        super().__init__(
            coordinator, eag_id, gateway_device_id, device, "device_id", "device_id"
        )
        self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> str:
        """Return the device ID."""
        return self._device.device_id


class OpusGreenNetLastSeenSensor(OpusGreenNetBaseSensor):
    """When the gateway last heard from the device."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the last-seen sensor."""
        super().__init__(
            coordinator, eag_id, gateway_device_id, device, "last_seen", "last_seen"
        )
        # Changes with every telegram: enabled on request only, so the
        # recorder and logbook are not filled for every device.
        self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> datetime | None:
        """Return the time of the newest report, unknown when not usable."""
        return parse_last_seen(self._device.last_seen)


class OpusGreenNetSettingsReadSensor(OpusGreenNetBaseSensor):
    """When the settings shown for the device were last read from it.

    The gateway reads the configuration of a device itself only now and then
    (nightly in the E0 catalog) and a device whose answer fails keeps its old
    values. The time is the gateway's cache time, or the time of the last
    *Read settings*. A value that is old, for example after a restore from a
    backup, is a reason to press *Read settings*.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the settings read sensor."""
        super().__init__(
            coordinator,
            eag_id,
            gateway_device_id,
            device,
            "settings_read",
            "settings_read",
        )

    @property
    def native_value(self) -> datetime | None:
        """Return the read time, unknown when the gateway lists none."""
        return self._device.settings_read_at


class OpusGreenNetWallSwitchLinksSensor(OpusGreenNetBaseSensor):
    """How many wall switch links an actuator has (read only)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
    ) -> None:
        """Initialize the link count sensor."""
        super().__init__(
            coordinator,
            eag_id,
            gateway_device_id,
            device,
            "wall_switch_links",
            "wall_switch_links",
        )
        self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> int | None:
        """Return the number of inbound links, unknown until a table was read.

        A wall switch linked to both channels of a two-channel actuator counts
        once per link. The linked devices themselves are never exposed here.
        """
        tables = self._device.link_tables
        return None if tables is None else len(tables.inbound)


class OpusGreenNetSettingSensor(OpusGreenNetBaseSensor):
    """A time or threshold stored on the device, shown read only.

    It reads the same channel field as the number entity of the setting, which
    stays disabled until someone wants to change the value. The value is as
    known from the last device list, the last confirmed write or the *Read
    settings* button.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
        device_time: DeviceTime,
    ) -> None:
        """Initialize the setting sensor."""
        key = f"{device_time.translation_key}_setting"
        super().__init__(coordinator, eag_id, gateway_device_id, device, key, key)
        self._device_time = device_time
        self._attr_native_unit_of_measurement = device_time.unit
        self._attr_device_class = SensorDeviceClass(device_time.device_class.value)
        self._attr_suggested_display_precision = SETTING_DISPLAY_PRECISION.get(
            device_time.translation_key
        )

    @property
    def native_value(self) -> float | None:
        """Return the stored value, or None while it is unknown."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        if channel is None:
            return None
        value: float | None = getattr(channel, self._device_time.attribute)
        if isinstance(value, float) and value.is_integer():
            # 300.0 would be shown with a decimal; a whole number is not.
            return int(value)
        return value


class OpusGreenNetSettingChoiceSensor(OpusGreenNetBaseSensor):
    """A setting with a fixed set of values stored on the switch, read only.

    A value the catalog does not list is shown as unknown, like the select.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
        select: DeviceSelect,
        values: tuple[str, ...],
    ) -> None:
        """Initialize the setting sensor."""
        key = f"{select.translation_key}_setting"
        super().__init__(coordinator, eag_id, gateway_device_id, device, key, key)
        self._select = select
        self._slugs = {value: option_slug(value) for value in values}
        self._attr_options = list(self._slugs.values())

    @property
    def native_value(self) -> str | None:
        """Return the stored option, or None while it is unknown or unlisted."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        if channel is None:
            return None
        value: str | None = getattr(channel, self._select.attribute)
        return None if value is None else self._slugs.get(value)


class OpusGreenNetMeasurementSensor(OpusGreenNetBaseSensor):
    """A finite, validated numeric reading from a sensor's default channel."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
        suffix: str,
        device_class: SensorDeviceClass,
        unit: str,
        *,
        entity_category: EntityCategory | None = None,
    ) -> None:
        super().__init__(coordinator, eag_id, gateway_device_id, device, suffix, suffix)
        self._suffix = suffix
        # Typed accessor instead of getattr(); an unknown suffix fails at setup.
        self._read_value = MEASUREMENT_READINGS[suffix]
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_entity_category = entity_category
        if (precision := MEASUREMENT_DISPLAY_PRECISION.get(suffix)) is not None:
            self._attr_suggested_display_precision = precision

    @property
    def native_value(self) -> float | None:
        """Return the parsed value, or unknown before the first valid reading."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        return self._read_value(channel) if channel is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, bool] | None:
        """Mark an illuminance above the sensor range (shown as its maximum)."""
        if self._suffix != "illuminance":
            return None
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        over_range = channel.illuminance_over_range if channel else None
        return None if over_range is None else {"over_range": over_range}


class OpusGreenNetHandleSensor(OpusGreenNetBaseSensor):
    """Handle position or the independent reported unlock request.

    The unlock request (Entsperranfrage) serves troubleshooting rather than
    everyday operation, so it is a diagnostic entity that is disabled by
    default. The default only applies when Home Assistant creates a new
    registry entry; existing entries keep their enablement, ID and
    customisations. Hiding this push-based entity sends no request to the
    device and is not a proven battery saving.
    """

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
        suffix: str,
    ) -> None:
        super().__init__(coordinator, eag_id, gateway_device_id, device, suffix, suffix)
        self._suffix = suffix
        if suffix == "handle_state":
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = ["open", "closed", "tilted"]
        elif suffix == "unlock_request":
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = ["requested", "not_requested"]
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
            self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> str | None:
        """Return the reported value independently of other handle attributes."""
        channel = self._device.channels.get(DEFAULT_CHANNEL)
        if channel is None:
            return None
        if self._suffix == "unlock_request":
            if channel.unlock_request is None:
                return None
            return "requested" if channel.unlock_request else "not_requested"
        if self._suffix == "handle_state":
            return channel.handle_state
        return None


class OpusGreenNetCoverRunSensor(OpusGreenNetEntity, RestoreSensor):
    """Seconds from a position command to the blind's report at the target.

    Measured by the integration for commands from Home Assistant and from
    other controllers, including reference runs. A run ended by Stop, without
    an end report or confirmed only by a status query records no time. The
    attributes tell the direction, start and end (Home Assistant scale,
    100 = open) and whether the run covered the full height.

    No device reports this value, so the last one is restored after a restart
    and shown until the next run completes.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "last_run_time"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_suggested_display_precision = 1
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: OpusGreenNetCoordinator,
        eag_id: str,
        gateway_device_id: str,
        device: EnOceanDevice,
        channel_id: int = DEFAULT_CHANNEL,
    ) -> None:
        """Initialize the run time sensor."""
        super().__init__(coordinator, eag_id, gateway_device_id, device, channel_id)
        channel_suffix = f"_ch{channel_id}" if device.channel_count > 1 else ""
        self._attr_unique_id = (
            f"{eag_id}_{device.device_id}_last_run_time{channel_suffix}"
        )
        self._restored_value: float | None = None
        self._restored_attributes: dict[str, object] | None = None

    async def async_added_to_hass(self) -> None:
        """Restore the last measured run from before the restart."""
        await super().async_added_to_hass()
        sensor_data = await self.async_get_last_sensor_data()
        if sensor_data is None or not isinstance(
            sensor_data.native_value, (int, float)
        ):
            return
        self._restored_value = float(sensor_data.native_value)
        if (last_state := await self.async_get_last_state()) is not None:
            self._restored_attributes = {
                key: last_state.attributes[key]
                for key in RUN_ATTRIBUTES
                if key in last_state.attributes
            } or None

    def _run(self) -> CoverRun | None:
        return self._coordinator.last_cover_runs.get(
            (self._device.device_id, self._channel_id)
        )

    @property
    def native_value(self) -> float | None:
        """Return the measured seconds of the last completed run."""
        run = self._run()
        return run.seconds if run is not None else self._restored_value

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        """Describe the last run (positions in Home Assistant scale)."""
        run = self._run()
        if run is None:
            return self._restored_attributes
        return {
            "direction": run.direction,
            "start_position": (
                None if run.start_position is None else 100 - run.start_position
            ),
            "end_position": 100 - run.target,
            "full_travel": run.full_travel,
            "finished_at": run.finished_at,
        }


# The boot time is derived from the uptime once a minute. Readings that only
# drift by network delay keep the stored time, so the state changes only when
# the gateway restarts.
UPTIME_DRIFT: Final = timedelta(seconds=60)


class OpusGreenNetGatewayUptimeSensor(SensorEntity):
    """When the gateway started, on the gateway's own device.

    The health probe reads the uptime once a minute; the sensor shows the
    resulting start time and changes only when the gateway restarts.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "gateway_uptime"

    def __init__(self, coordinator: OpusGreenNetCoordinator, eag_id: str) -> None:
        """Initialize the sensor for the gateway ``eag_id``."""
        self._coordinator = coordinator
        self._eag_id = eag_id
        self._attr_unique_id = f"{eag_id}_gateway_uptime"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, eag_id)})
        self._started: datetime | None = None

    @property
    def available(self) -> bool:
        """Return whether the gateway is reachable."""
        return self._coordinator.available

    @property
    def native_value(self) -> datetime | None:
        """Return when the gateway started."""
        return self._started

    def _update_started(self) -> bool:
        """Derive the start time from the latest uptime; True if it changed."""
        uptime = self._coordinator.gateway_uptime
        if uptime is None:
            return False
        started = (dt_util.utcnow() - timedelta(seconds=float(uptime))).replace(
            microsecond=0
        )
        if self._started is not None and abs(started - self._started) <= UPTIME_DRIFT:
            return False
        self._started = started
        return True

    async def async_added_to_hass(self) -> None:
        """Show a restart of the gateway and every availability change."""
        await super().async_added_to_hass()
        self._update_started()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_GATEWAY_UPTIME_UPDATE}_{self._eag_id}",
                self._handle_uptime,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_AVAILABILITY_UPDATE}_{self._eag_id}",
                self.async_write_ha_state,
            )
        )

    @callback
    def _handle_uptime(self) -> None:
        if self._update_started():
            self.async_write_ha_state()


def _info_text(coordinator: OpusGreenNetCoordinator, key: str) -> str | None:
    """Return a text field of the gateway information, or None."""
    value = coordinator.gateway_info.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    return value if isinstance(value, str) and value.strip() else None


# Identity, versions and network address of the gateway: key (also the
# translation key and the end of the unique ID) -> reading.
GATEWAY_INFO_READINGS: Final[
    dict[str, Callable[[OpusGreenNetCoordinator], str | None]]
] = {
    "gateway_eurid": lambda coordinator: coordinator.eag_id,
    "gateway_firmware_version": lambda coordinator: _info_text(coordinator, "version"),
    "gateway_app_version": lambda coordinator: _info_text(coordinator, "appVersion"),
    "gateway_api_version": lambda coordinator: _info_text(coordinator, "apiVersion"),
    "gateway_hostname": lambda coordinator: coordinator.gateway_address[0],
    "gateway_ipv4_address": lambda coordinator: coordinator.gateway_address[1],
    "gateway_ipv6_address": lambda coordinator: coordinator.gateway_address[2],
}

# Disabled by default: the EURID is the gateway's default password, the others
# only repeat what the entry already shows.
GATEWAY_INFO_DISABLED_BY_DEFAULT: Final = frozenset(
    {
        "gateway_eurid",
        "gateway_hostname",
        "gateway_ipv4_address",
        "gateway_ipv6_address",
    }
)


class OpusGreenNetGatewayInfoSensor(SensorEntity):
    """One identity, version or address reading of the gateway.

    Diagnostic sensors on the gateway's own device. The gateway information is
    read at setup and every 30 minutes, the address is resolved on the same
    schedule; nothing is polled by the entity.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: OpusGreenNetCoordinator, eag_id: str, key: str
    ) -> None:
        """Initialize the sensor ``key`` of GATEWAY_INFO_READINGS."""
        self._coordinator = coordinator
        self._eag_id = eag_id
        self._reading = GATEWAY_INFO_READINGS[key]
        self._attr_translation_key = key
        self._attr_unique_id = f"{eag_id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, eag_id)})
        self._attr_entity_registry_enabled_default = (
            key not in GATEWAY_INFO_DISABLED_BY_DEFAULT
        )

    @property
    def available(self) -> bool:
        """Return whether the gateway is reachable."""
        return self._coordinator.available

    @property
    def native_value(self) -> str | None:
        """Return the reading, or None while the gateway did not report it."""
        return self._reading(self._coordinator)

    async def async_added_to_hass(self) -> None:
        """Show every new reading and every availability change."""
        await super().async_added_to_hass()
        for signal in (SIGNAL_GATEWAY_INFO_UPDATE, SIGNAL_AVAILABILITY_UPDATE):
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass, f"{signal}_{self._eag_id}", self._handle_update
                )
            )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
