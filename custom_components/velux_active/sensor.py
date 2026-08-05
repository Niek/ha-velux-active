"""Sensor platform for Velux Active with Netatmo."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    LIGHT_LUX,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfRatio,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONTROL_URL, DOMAIN, MANUFACTURER
from .coordinator import VeluxActiveConfigEntry, VeluxActiveDataUpdateCoordinator
from .entity import gateway_device_info

FOOT_CANDLES_TO_LUX = 10.764


@dataclass(frozen=True, kw_only=True)
class VeluxRoomSensorDescription(SensorEntityDescription):
    """Describes a VELUX room climate sensor."""

    value_fn: Callable[[dict[str, Any]], Any]


@dataclass(frozen=True, kw_only=True)
class VeluxDiagnosticSensorDescription(SensorEntityDescription):
    """Describes an opt-in VELUX diagnostic sensor."""

    source_key: str
    value_fn: Callable[[Any], Any] | None = None


def _tenths_to_celsius(room: dict[str, Any]) -> float | None:
    return _tenths_value_to_celsius(room.get("temperature"))


def _tenths_value_to_celsius(value: Any) -> float | None:
    """Convert a VELUX tenths-of-degree value to Celsius."""
    return value / 10 if isinstance(value, (int, float)) else None


def _velux_illuminance_to_lux(room: dict[str, Any]) -> float | None:
    value = room.get("lux")
    if not isinstance(value, (int, float)):
        return None

    # IMPORTANT: Although the API calls this field "lux", observed values appear
    # to be foot-candles. VELUX and the APK do not confirm that unit, so this
    # conversion is based on field measurements reported here:
    # https://github.com/Niek/ha-velux-active/issues/22
    return value * FOOT_CANDLES_TO_LUX


def _millivolts_to_volts(value: Any) -> float | None:
    """Convert the API's millivolt battery level to volts."""
    return value / 1000 if isinstance(value, (int, float)) else None


def _rssi_to_dbm(value: Any) -> int | float | None:
    """Convert an RSSI value or positive magnitude to dBm."""
    return -abs(value) if isinstance(value, (int, float)) else None


ROOM_SENSORS: tuple[VeluxRoomSensorDescription, ...] = (
    VeluxRoomSensorDescription(
        key="temperature",
        name="Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_tenths_to_celsius,
    ),
    VeluxRoomSensorDescription(
        key="co2",
        name="CO2",
        device_class=SensorDeviceClass.CO2,
        native_unit_of_measurement=UnitOfRatio.PARTS_PER_MILLION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda room: room.get("co2"),
    ),
    VeluxRoomSensorDescription(
        key="humidity",
        name="Humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda room: room.get("humidity"),
    ),
    VeluxRoomSensorDescription(
        key="lux",
        name="Illuminance",
        device_class=SensorDeviceClass.ILLUMINANCE,
        native_unit_of_measurement=LIGHT_LUX,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=_velux_illuminance_to_lux,
    ),
    VeluxRoomSensorDescription(
        key="air_quality",
        name="Air quality index",
        device_class=SensorDeviceClass.AQI,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda room: room.get("air_quality"),
    ),
)

MODULE_MODELS = {
    "NXS": "Indoor Climate Sensor",
    "NXD": "Departure Switch",
}

BATTERY_STATE_OPTIONS = ["full", "high", "medium", "low", "very_low"]
RF_STATE_OPTIONS = ["full", "high", "medium", "low", "very_low"]
WIFI_STATE_OPTIONS = ["full", "high", "medium", "low"]

MODULE_DIAGNOSTIC_SENSORS: tuple[VeluxDiagnosticSensorDescription, ...] = (
    VeluxDiagnosticSensorDescription(
        key="battery_voltage",
        name="Battery voltage",
        source_key="battery_level",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=3,
        value_fn=_millivolts_to_volts,
    ),
    VeluxDiagnosticSensorDescription(
        key="battery_state",
        name="Battery state",
        source_key="battery_state",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        options=BATTERY_STATE_OPTIONS,
    ),
    VeluxDiagnosticSensorDescription(
        key="rf_signal_strength",
        name="RF signal strength",
        source_key="rf_strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_rssi_to_dbm,
    ),
    VeluxDiagnosticSensorDescription(
        key="rf_signal_quality",
        name="RF signal quality",
        source_key="rf_state",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        options=RF_STATE_OPTIONS,
    ),
)

GATEWAY_DIAGNOSTIC_SENSORS: tuple[VeluxDiagnosticSensorDescription, ...] = (
    VeluxDiagnosticSensorDescription(
        key="wifi_signal_strength",
        name="Wi-Fi signal strength",
        source_key="wifi_strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_rssi_to_dbm,
    ),
    VeluxDiagnosticSensorDescription(
        key="wifi_signal_quality",
        name="Wi-Fi signal quality",
        source_key="wifi_state",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        options=WIFI_STATE_OPTIONS,
    ),
)

MODULE_DIAGNOSTIC_ATTRIBUTES = (
    "battery_level",
    "battery_state",
    "last_seen",
    "reachable",
    "rf_state",
    "rf_strength",
)

ROOM_DIAGNOSTIC_ATTRIBUTES = (
    "algo_status",
    "auto_close_ts",
    "min_comfort_humidity",
    "max_comfort_humidity",
    "max_comfort_co2",
)
ROOM_TEMPERATURE_ATTRIBUTES = (
    "min_comfort_temperature",
    "max_comfort_temperature",
)


def _diagnostic_value(
    description: VeluxDiagnosticSensorDescription, data: dict[str, Any]
) -> Any:
    """Return a converted diagnostic value valid for its entity description."""
    value = data.get(description.source_key)
    if description.value_fn is not None:
        value = description.value_fn(value)
    if description.options is not None and value not in description.options:
        return None
    return value


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeluxActiveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up VELUX room climate and module battery sensors."""
    coordinator = entry.runtime_data
    room_entities: set[tuple[str, str]] = set()
    battery_entities: set[str] = set()
    module_diagnostic_entities: set[tuple[str, str]] = set()
    gateway_diagnostic_entities: set[tuple[str, str]] = set()

    @callback
    def _async_add_new_entities() -> None:
        entities: list[SensorEntity] = []

        for room_key, room in sorted(coordinator.data.rooms.items()):
            for description in ROOM_SENSORS:
                entity_key = (room_key, description.key)
                if description.key not in room or entity_key in room_entities:
                    continue
                entities.append(VeluxRoomSensor(coordinator, room_key, description))
                room_entities.add(entity_key)

        for module_id, module in sorted(coordinator.data.sensor_modules.items()):
            if "battery_percent" in module and module_id not in battery_entities:
                entities.append(VeluxModuleBatterySensor(coordinator, module_id))
                battery_entities.add(module_id)

            for description in MODULE_DIAGNOSTIC_SENSORS:
                entity_key = (module_id, description.key)
                if (
                    description.source_key not in module
                    or entity_key in module_diagnostic_entities
                ):
                    continue
                entities.append(
                    VeluxModuleDiagnosticSensor(coordinator, module_id, description)
                )
                module_diagnostic_entities.add(entity_key)

        for gateway_id, gateway in sorted(coordinator.data.gateway_status.items()):
            for description in GATEWAY_DIAGNOSTIC_SENSORS:
                entity_key = (gateway_id, description.key)
                if (
                    description.source_key not in gateway
                    or entity_key in gateway_diagnostic_entities
                ):
                    continue
                entities.append(
                    VeluxGatewayDiagnosticSensor(coordinator, gateway_id, description)
                )
                gateway_diagnostic_entities.add(entity_key)

        if entities:
            async_add_entities(entities)

    _async_add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_entities))


class VeluxRoomSensor(
    CoordinatorEntity[VeluxActiveDataUpdateCoordinator], SensorEntity
):
    """A climate measurement for one room from raw homestatus data."""

    _attr_has_entity_name = True
    entity_description: VeluxRoomSensorDescription

    def __init__(
        self,
        coordinator: VeluxActiveDataUpdateCoordinator,
        room_key: str,
        description: VeluxRoomSensorDescription,
    ) -> None:
        """Initialize the room sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._room_key = room_key
        self._attr_unique_id = f"{room_key}_{description.key}"

    @property
    def _room(self) -> dict[str, Any]:
        """Return the latest room data for this sensor."""
        return self.coordinator.data.rooms.get(self._room_key, {})

    @property
    def device_info(self) -> DeviceInfo:
        """Group all measurements of a room under one virtual device."""
        room = self._room
        return DeviceInfo(
            configuration_url=CONTROL_URL,
            identifiers={(DOMAIN, f"room-{self._room_key}")},
            manufacturer=MANUFACTURER,
            model="Room Climate",
            name=room.get("name") or f"Room {room.get('id', self._room_key)}",
            suggested_area=room.get("name"),
        )

    @property
    def native_value(self) -> Any:
        """Return the current measurement."""
        return self.entity_description.value_fn(self._room)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose room comfort thresholds and VELUX algorithm diagnostics."""
        room = self._room
        attributes = {
            key: room[key] for key in ROOM_DIAGNOSTIC_ATTRIBUTES if key in room
        }
        for key in ROOM_TEMPERATURE_ATTRIBUTES:
            if key in room:
                attributes[f"{key}_celsius"] = _tenths_value_to_celsius(room[key])
        return attributes

    @property
    def available(self) -> bool:
        """Return True while the room is present in coordinator data."""
        return super().available and self._room_key in self.coordinator.data.rooms


class VeluxModuleBatterySensor(
    CoordinatorEntity[VeluxActiveDataUpdateCoordinator], SensorEntity
):
    """Battery level of a battery-powered VELUX module."""

    _attr_has_entity_name = True
    _attr_name = "Battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: VeluxActiveDataUpdateCoordinator,
        module_id: str,
    ) -> None:
        """Initialize the battery sensor."""
        super().__init__(coordinator)
        self._module_id = module_id
        self._attr_unique_id = f"{module_id}_battery"

    @property
    def _module(self) -> dict[str, Any]:
        """Return the latest module data for this sensor."""
        return self.coordinator.data.sensor_modules.get(self._module_id, {})

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for the physical sensor module."""
        return _module_device_info(self._module_id, self._module)

    @property
    def native_value(self) -> int | None:
        """Return the battery percentage."""
        return self._module.get("battery_percent")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose battery and RF diagnostics."""
        module = self._module
        return {
            key: module[key] for key in MODULE_DIAGNOSTIC_ATTRIBUTES if key in module
        }

    @property
    def available(self) -> bool:
        """Return True while the module is present in coordinator data."""
        return (
            super().available
            and self._module_id in self.coordinator.data.sensor_modules
        )


class VeluxModuleDiagnosticSensor(
    CoordinatorEntity[VeluxActiveDataUpdateCoordinator], SensorEntity
):
    """An opt-in diagnostic sensor for a battery-powered VELUX module."""

    _attr_has_entity_name = True
    entity_description: VeluxDiagnosticSensorDescription

    def __init__(
        self,
        coordinator: VeluxActiveDataUpdateCoordinator,
        module_id: str,
        description: VeluxDiagnosticSensorDescription,
    ) -> None:
        """Initialize the module diagnostic sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._module_id = module_id
        self._attr_unique_id = f"{module_id}_{description.key}"

    @property
    def _module(self) -> dict[str, Any]:
        """Return the latest module data for this sensor."""
        return self.coordinator.data.sensor_modules.get(self._module_id, {})

    @property
    def device_info(self) -> DeviceInfo:
        """Attach to the physical sensor module."""
        return _module_device_info(self._module_id, self._module)

    @property
    def native_value(self) -> Any:
        """Return the converted diagnostic value."""
        return _diagnostic_value(self.entity_description, self._module)

    @property
    def available(self) -> bool:
        """Return whether the diagnostic field is present in current data."""
        return super().available and self.entity_description.source_key in self._module


class VeluxGatewayDiagnosticSensor(
    CoordinatorEntity[VeluxActiveDataUpdateCoordinator], SensorEntity
):
    """An opt-in diagnostic sensor for a VELUX gateway."""

    _attr_has_entity_name = True
    entity_description: VeluxDiagnosticSensorDescription

    def __init__(
        self,
        coordinator: VeluxActiveDataUpdateCoordinator,
        gateway_id: str,
        description: VeluxDiagnosticSensorDescription,
    ) -> None:
        """Initialize the gateway diagnostic sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._gateway_id = gateway_id
        self._attr_unique_id = f"{gateway_id}_{description.key}"

    @property
    def _gateway(self) -> dict[str, Any]:
        """Return the latest raw gateway status."""
        return self.coordinator.data.gateway_status.get(self._gateway_id, {})

    @property
    def device_info(self) -> DeviceInfo:
        """Attach to the gateway device."""
        return gateway_device_info(self._gateway_id)

    @property
    def native_value(self) -> Any:
        """Return the converted diagnostic value."""
        return _diagnostic_value(self.entity_description, self._gateway)

    @property
    def available(self) -> bool:
        """Return whether the diagnostic field is present in current data."""
        return super().available and self.entity_description.source_key in self._gateway


def _module_device_info(module_id: str, module: dict[str, Any]) -> DeviceInfo:
    """Return HA device metadata for a physical VELUX sensor module."""
    firmware_revision = module.get("firmware_revision")
    return DeviceInfo(
        configuration_url=CONTROL_URL,
        identifiers={(DOMAIN, module_id)},
        manufacturer=MANUFACTURER,
        model=MODULE_MODELS.get(module.get("type"), "Sensor Module"),
        name=module.get("name") or module_id,
        suggested_area=module.get("room_name"),
        sw_version=str(firmware_revision) if firmware_revision is not None else None,
    )
