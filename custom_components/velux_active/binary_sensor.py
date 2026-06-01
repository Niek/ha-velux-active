"""Binary sensor platform for Velux Active - rain detection via NXG gateway."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import VeluxActiveDataUpdateCoordinator
from .entity import VeluxActiveGatewayEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Velux Active binary sensors from a config entry."""
    coordinator: VeluxActiveDataUpdateCoordinator = entry.runtime_data
    async_add_entities(
        VeluxActiveRainSensor(coordinator, module_id)
        for module_id, module in coordinator.data.gateways.items()
        if getattr(module, "is_raining", None) is not None
    )


class VeluxActiveRainSensor(VeluxActiveGatewayEntity, BinarySensorEntity):
    """Rain sensor derived from the is_raining attribute of the NXG gateway."""

    _attr_device_class = BinarySensorDeviceClass.MOISTURE
    _attr_name = "Rain"

    def __init__(
        self,
        coordinator: VeluxActiveDataUpdateCoordinator,
        module_id: str,
    ) -> None:
        """Initialize the rain sensor."""
        super().__init__(coordinator, module_id)
        self._attr_unique_id = f"{module_id}_rain"

    @property
    def is_on(self) -> bool | None:
        """Return True if it is currently raining."""
        return self.module.is_raining
