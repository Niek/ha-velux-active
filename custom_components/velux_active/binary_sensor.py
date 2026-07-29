"""Binary sensor platform for Velux Active with Netatmo."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import VeluxActiveConfigEntry, VeluxActiveDataUpdateCoordinator
from .entity import gateway_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeluxActiveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors for each VELUX gateway."""
    coordinator = entry.runtime_data
    async_add_entities(
        VeluxGatewayConnectivityBinarySensor(coordinator, gateway_id)
        for gateway_id in coordinator.data.gateway_connectivity
    )
    async_add_entities(
        VeluxGatewayRainBinarySensor(coordinator, gateway_id)
        for gateway_id in coordinator.data.gateway_connectivity
    )


class VeluxGatewayConnectivityBinarySensor(
    CoordinatorEntity[VeluxActiveDataUpdateCoordinator], BinarySensorEntity
):
    """Connectivity state reported for a VELUX gateway."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: VeluxActiveDataUpdateCoordinator,
        gateway_id: str,
    ) -> None:
        """Initialize the connectivity sensor."""
        super().__init__(coordinator)
        self._gateway_id = gateway_id
        self._attr_unique_id = f"{gateway_id}_connectivity"

    @property
    def device_info(self) -> DeviceInfo:
        """Attach to the gateway device."""
        return gateway_device_info(self._gateway_id)

    @property
    def is_on(self) -> bool | None:
        """Return whether the gateway is connected."""
        return self.coordinator.data.gateway_connectivity.get(self._gateway_id)


class VeluxGatewayRainBinarySensor(
    CoordinatorEntity[VeluxActiveDataUpdateCoordinator], BinarySensorEntity
):
    """Rain state reported by a VELUX gateway."""

    _attr_has_entity_name = True
    _attr_name = "Reported rain"
    _attr_device_class = BinarySensorDeviceClass.MOISTURE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: VeluxActiveDataUpdateCoordinator,
        gateway_id: str,
    ) -> None:
        """Initialize the reported-rain sensor."""
        super().__init__(coordinator)
        self._gateway_id = gateway_id
        self._attr_unique_id = f"{gateway_id}_rain"

    @property
    def device_info(self) -> DeviceInfo:
        """Attach to the gateway device."""
        return gateway_device_info(self._gateway_id)

    @property
    def is_on(self) -> bool | None:
        """Return whether the gateway currently reports rain."""
        for home in self.coordinator.data.homes.values():
            gateway = home.modules.get(self._gateway_id)
            if gateway is not None:
                return getattr(gateway, "is_raining", None)
        return None

    @property
    def available(self) -> bool:
        """Return whether current rain state is available."""
        return (
            super().available
            and self.coordinator.data.gateway_connectivity.get(self._gateway_id) is True
            and self.is_on is not None
        )
