"""Binary sensor platform for Velux Active - rain detection via NXG gateway."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import VeluxActiveConfigEntry, VeluxActiveDataUpdateCoordinator
from .entity import VeluxActiveGatewayEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeluxActiveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one rain sensor per NXG gateway that exposes is_raining."""
    coordinator: VeluxActiveDataUpdateCoordinator = entry.runtime_data
    async_add_entities(
        VeluxActiveRainSensor(coordinator, gateway_id)
        for gateway_id, gateway in coordinator.data.gateways.items()
        if getattr(gateway, "is_raining", None) is not None
    )


class VeluxActiveRainSensor(VeluxActiveGatewayEntity, BinarySensorEntity):
    """Rain sensor sourced from the NXG gateway's is_raining attribute."""

    _attr_device_class = BinarySensorDeviceClass.MOISTURE

    def __init__(
        self,
        coordinator: VeluxActiveDataUpdateCoordinator,
        gateway_id: str,
    ) -> None:
        """Initialize the rain sensor."""
        super().__init__(coordinator, gateway_id)
        self._attr_unique_id = f"{gateway_id}_rain"
        self._attr_name = "Rain"

    @property
    def is_on(self) -> bool | None:
        """Return True if the gateway reports rain."""
        return self.module.is_raining
