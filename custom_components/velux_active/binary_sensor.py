"""Binary sensor platform for Velux Active - rain detection per cover."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import VeluxActiveDataUpdateCoordinator
from .entity import VeluxActiveEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one rain sensor per cover, sourced from the home's gateway."""
    coordinator: VeluxActiveDataUpdateCoordinator = entry.runtime_data

    gateway_id = next(
        (
            gid
            for gid, gw in coordinator.data.gateways.items()
            if getattr(gw, "is_raining", None) is not None
        ),
        None,
    )
    if gateway_id is None:
        return

    async_add_entities(
        VeluxActiveCoverRainSensor(coordinator, module_id, gateway_id)
        for module_id in coordinator.data.covers
    )


class VeluxActiveCoverRainSensor(VeluxActiveEntity, BinarySensorEntity):
    """Rain sensor shown under each cover device, reading from the NXG gateway."""

    _attr_device_class = BinarySensorDeviceClass.MOISTURE
    _attr_name = "Rain"

    def __init__(
        self,
        coordinator: VeluxActiveDataUpdateCoordinator,
        module_id: str,
        gateway_id: str,
    ) -> None:
        """Initialize the rain sensor."""
        super().__init__(coordinator, module_id)
        self._gateway_id = gateway_id
        self._attr_unique_id = f"{module_id}_rain"

    @property
    def is_on(self) -> bool | None:
        """Return True if the gateway reports rain."""
        gateway = self.coordinator.data.gateways.get(self._gateway_id)
        return gateway.is_raining if gateway is not None else None
