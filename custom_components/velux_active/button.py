"""Button platform for Velux Active with Netatmo.

Provides a button to move each roof window to its configured secure position
(a partially open position that allows ventilation but is too small to
climb through). The secure position value is set in the Velux app.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import VeluxActiveConfigEntry
from .cover import VeluxActiveCover, _module_is_window
from .entity import VeluxActiveEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeluxActiveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up secure position buttons for each roof window."""
    coordinator = entry.runtime_data

    entities = [
        VeluxSecurePositionButton(coordinator, module_id)
        for module_id, module in coordinator.data.covers.items()
        if _module_is_window(module_id, module)
    ]
    async_add_entities(entities)


class VeluxSecurePositionButton(VeluxActiveEntity, ButtonEntity):
    """Button that moves a window to its configured secure ventilation position."""

    _attr_icon = "mdi:shield-lock"

    def __init__(self, coordinator, module_id: str) -> None:
        super().__init__(coordinator, module_id)
        self._attr_unique_id = f"{module_id}_secure_position_button"
        self._attr_name = "Go to Secure Position"

    async def async_press(self) -> None:
        """Move the window to its secure position."""
        secure_pos = getattr(self.module, "secure_position", None)
        if secure_pos is None:
            raise HomeAssistantError(
                f"Secure position not available for {self.module.name}"
            )

        # Reuse the cover entity's signed setstate logic by instantiating
        # a temporary cover entity for this module and calling its move method
        cover = VeluxActiveCover(self.coordinator, self._module_id)

        _LOGGER.debug(
            "Moving %s to secure position %d%%",
            self.module.name,
            secure_pos,
        )

        # secure_position is a raw API value (same scale as target_position)
        # Convert to HA position and use the cover's move method
        await cover._move_to_ha_position(secure_pos)
