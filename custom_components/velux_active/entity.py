"""Base entity for Velux Active with Netatmo."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONTROL_URL, DOMAIN, MANUFACTURER
from .coordinator import VeluxActiveDataUpdateCoordinator


class VeluxActiveEntity(CoordinatorEntity[VeluxActiveDataUpdateCoordinator]):
    """Shared entity helpers for VELUX ACTIVE devices."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: VeluxActiveDataUpdateCoordinator,
        module_id: str,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._module_id = module_id
        self._attr_unique_id = module_id
        self._attr_name = None

    @property
    def module(self):
        """Return the current pyatmo module."""
        return self.coordinator.data.covers[self._module_id]

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for the module."""
        model = (self.module.velux_type or "cover").replace("_", " ").title()
        return DeviceInfo(
            configuration_url=CONTROL_URL,
            identifiers={(DOMAIN, self.module.entity_id)},
            manufacturer=MANUFACTURER,
            model=model,
            name=self.module.name,
            sw_version=str(getattr(self.module, "firmware_revision", "")) or None,
        )
