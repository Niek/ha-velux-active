"""Switch platform for VELUX Active automatic ventilation."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .cover import _module_is_window
from .coordinator import VeluxActiveConfigEntry, VeluxActiveDataUpdateCoordinator
from .entity import VeluxActiveEntity

_LOGGER = logging.getLogger(__name__)

MODE_ALGO_ENABLED = "auto"
MODE_ALGO_DISABLED = "algo_disabled"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeluxActiveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up VELUX automatic ventilation switches (one per roof window)."""
    coordinator = entry.runtime_data
    entities = [
        VeluxAlgorithmSwitch(coordinator, module_id)
        for module_id, module in sorted(coordinator.data.covers.items())
        if _module_is_window(module_id, module)
    ]
    async_add_entities(entities)


class VeluxAlgorithmSwitch(VeluxActiveEntity, SwitchEntity):
    """Switch to enable or disable automatic ventilation for a window."""

    _attr_icon = "mdi:air-filter"
    _attr_name = "Auto Ventilation"

    def __init__(
        self,
        coordinator: VeluxActiveDataUpdateCoordinator,
        module_id: str,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, module_id)
        self._attr_unique_id = f"{module_id}_auto_ventilation"

    @property
    def is_on(self) -> bool | None:
        """Return True when automatic ventilation is active."""
        mode = getattr(self.module, "mode", None)
        if mode is None:
            return None
        return mode != MODE_ALGO_DISABLED

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable automatic ventilation."""
        await self._async_set_mode(MODE_ALGO_ENABLED)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable automatic ventilation."""
        await self._async_set_mode(MODE_ALGO_DISABLED)

    async def _async_set_mode(self, mode: str) -> None:
        """Send an unsigned mode setstate command for this window."""
        home = self._get_home()
        bridge_id = self._get_bridge_id(home) if home is not None else None
        if home is None or bridge_id is None:
            raise HomeAssistantError("Could not find home or gateway")

        _LOGGER.debug(
            "Setting automatic ventilation mode %s for %s", mode, self._module_id
        )
        await self._async_setstate(
            home,
            [{"id": self._module_id, "bridge": bridge_id, "mode": mode}],
            action="Auto ventilation command",
        )

        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
