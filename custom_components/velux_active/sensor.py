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
    CONCENTRATION_PARTS_PER_MILLION,
    LIGHT_LUX,
    PERCENTAGE,
    EntityCategory,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONTROL_URL, DOMAIN, MANUFACTURER
from .coordinator import VeluxActiveConfigEntry, VeluxActiveDataUpdateCoordinator

FOOT_CANDLES_TO_LUX = 10.764


@dataclass(frozen=True, kw_only=True)
class VeluxRoomSensorDescription(SensorEntityDescription):
    """Describes a VELUX room climate sensor."""

    value_fn: Callable[[dict[str, Any]], Any]


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
        native_unit_of_measurement=CONCENTRATION_PARTS_PER_MILLION,
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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeluxActiveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up VELUX room climate and module battery sensors."""
    coordinator = entry.runtime_data
    room_entities: set[tuple[str, str]] = set()
    module_entities: set[str] = set()

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
            if "battery_percent" not in module or module_id in module_entities:
                continue
            entities.append(VeluxModuleBatterySensor(coordinator, module_id))
            module_entities.add(module_id)

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
