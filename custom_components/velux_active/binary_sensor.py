"""Binary sensor platform for Velux Active with Netatmo.

Exposes the VELUX gateway rain detector as a HA binary sensor.
When active, the gateway will automatically close windows to the
configured rain position.
"""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONTROL_URL, DOMAIN, MANUFACTURER
from .coordinator import VeluxActiveConfigEntry, VeluxActiveDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


def _get_bridge(coordinator):
    """Return the NXG gateway module or None."""
    for home in coordinator.client._account.homes.values():
        for module_id, module in home.modules.items():
            if type(module).__name__ == "NXG":
                return module_id, module
    return None, None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeluxActiveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Velux rain binary sensor."""
    coordinator = entry.runtime_data
    bridge_id, bridge = _get_bridge(coordinator)
    if bridge is not None:
        async_add_entities([VeluxRainSensor(coordinator, bridge_id)])


class VeluxRainSensor(CoordinatorEntity[VeluxActiveDataUpdateCoordinator], BinarySensorEntity):
    """Binary sensor for the VELUX gateway rain detector.

    When on, rain has been detected and the gateway will automatically
    move windows to their configured rain position.
    """

    _attr_has_entity_name = True
    _attr_name = "Rain Detected"
    _attr_device_class = BinarySensorDeviceClass.MOISTURE

    def __init__(
        self,
        coordinator: VeluxActiveDataUpdateCoordinator,
        bridge_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._bridge_id = bridge_id
        self._attr_unique_id = f"{DOMAIN}_rain_sensor"

    @property
    def is_on(self) -> bool | None:
        """Return True if rain is currently detected."""
        _, bridge = _get_bridge(self.coordinator)
        if bridge is None:
            return None
        return bool(getattr(bridge, "is_raining", False))

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            configuration_url=CONTROL_URL,
            connections={(CONNECTION_NETWORK_MAC, self._bridge_id)},
            identifiers={(DOMAIN, self._bridge_id)},
            manufacturer=MANUFACTURER,
            model="VELUX Gateway",
            name="VELUX Gateway",
        )
