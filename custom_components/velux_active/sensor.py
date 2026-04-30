"""Sensor platform for Velux Active with Netatmo.

Exposes read-only properties from the VELUX gateway and window modules:
- Gateway: is_raining (binary), wifi_strength
- Windows: rain_position, secure_position
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import logging

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONTROL_URL, DOMAIN, MANUFACTURER
from .coordinator import VeluxActiveConfigEntry, VeluxActiveDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeluxActiveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Velux Active sensor entities."""
    coordinator = entry.runtime_data
    entities: list = []

    # Per-window sensors
    for module_id, module in coordinator.data.covers.items():
        if type(module).__name__ == "NXO" and getattr(module, "velux_type", "") == "window":
            entities.append(VeluxRainPositionSensor(coordinator, module_id))
            entities.append(VeluxSecurePositionSensor(coordinator, module_id))

    async_add_entities(entities)


def _get_bridge(coordinator):
    """Return the NXG gateway module or None."""
    for home in coordinator.client._account.homes.values():
        for module_id, module in home.modules.items():
            if type(module).__name__ == "NXG":
                return module
    return None


def _get_bridge_id(coordinator) -> str | None:
    """Return the NXG gateway module ID or None."""
    for home in coordinator.client._account.homes.values():
        for module_id, module in home.modules.items():
            if type(module).__name__ == "NXG":
                return module_id
    return None


# ---------------------------------------------------------------------------
# Per-window sensors — rain_position and secure_position
# ---------------------------------------------------------------------------

class VeluxWindowSensorBase(CoordinatorEntity[VeluxActiveDataUpdateCoordinator], SensorEntity):
    """Base class for per-window read-only sensors."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"

    def __init__(
        self,
        coordinator: VeluxActiveDataUpdateCoordinator,
        module_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._module_id = module_id

    @property
    def module(self):
        return self.coordinator.data.covers[self._module_id]

    @property
    def device_info(self) -> DeviceInfo:
        bridge_id = _get_bridge_id(self.coordinator)
        model = (getattr(self.module, "velux_type", "cover") or "cover").replace("_", " ").title()
        return DeviceInfo(
            configuration_url=CONTROL_URL,
            identifiers={(DOMAIN, self.module.entity_id)},
            manufacturer=MANUFACTURER,
            model=model,
            name=self.module.name,
            sw_version=str(getattr(self.module, "firmware_revision", "") or ""),
        )


class VeluxRainPositionSensor(VeluxWindowSensorBase):
    """Sensor showing the position a window moves to when rain is detected."""

    _attr_icon = "mdi:weather-rainy"

    def __init__(self, coordinator, module_id: str) -> None:
        super().__init__(coordinator, module_id)
        self._attr_unique_id = f"{module_id}_rain_position"
        self._attr_name = "Rain Position"

    @property
    def native_value(self) -> int | None:
        return getattr(self.module, "rain_position", None)


class VeluxSecurePositionSensor(VeluxWindowSensorBase):
    """Sensor showing the secure ventilation position of a window."""

    _attr_icon = "mdi:shield-lock"

    def __init__(self, coordinator, module_id: str) -> None:
        super().__init__(coordinator, module_id)
        self._attr_unique_id = f"{module_id}_secure_position"
        self._attr_name = "Secure Position"

    @property
    def native_value(self) -> int | None:
        return getattr(self.module, "secure_position", None)

