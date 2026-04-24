"""Cover platform for Velux Active with Netatmo."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pyatmo.exceptions import ApiError

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import VeluxActiveConfigEntry
from .entity import VeluxActiveEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeluxActiveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up VELUX covers from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        VeluxActiveCover(coordinator, module_id)
        for module_id in sorted(coordinator.data.covers)
    )


class VeluxActiveCover(VeluxActiveEntity, CoverEntity):
    """Representation of a VELUX ACTIVE cover."""

    _attr_device_class = CoverDeviceClass.SHUTTER
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    def __init__(self, coordinator, module_id: str) -> None:
        """Initialize the cover."""
        super().__init__(coordinator, module_id)
        # Keep HA responsive between 30s cloud polls after a command is accepted.
        self._motion_state: str | None = None
        self._motion_target_position: int | None = None

    @property
    def current_cover_position(self) -> int | None:
        """Return the current cover position."""
        return self.module.current_position

    @property
    def is_opening(self) -> bool | None:
        """Return whether the cover is opening."""
        return self._motion_direction() == "opening"

    @property
    def is_closing(self) -> bool | None:
        """Return whether the cover is closing."""
        return self._motion_direction() == "closing"

    @property
    def is_closed(self) -> bool | None:
        """Return whether the cover is closed."""
        position = self.current_cover_position
        return None if position is None else position == 0

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        await self._async_run_command(self.module.async_open, target_position=100)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        await self._async_run_command(self.module.async_close, target_position=0)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        await self._async_run_command(self.module.async_stop, target_position=None)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover to a position."""
        position = kwargs[ATTR_POSITION]
        await self._async_run_command(
            self.module.async_set_target_position,
            position,
            target_position=position,
        )

    def _motion_direction(self) -> str | None:
        """Return the current movement direction from live or optimistic data."""
        current = self.module.current_position
        target = self.module.target_position

        if current is not None and target is not None and current != target:
            return "opening" if target > current else "closing"

        return self._motion_state

    def _set_motion_state(self, target_position: int | None) -> None:
        """Set an optimistic motion state after a command."""
        current = self.module.current_position
        if target_position is None or current is None or target_position == current:
            self._motion_state = None
            self._motion_target_position = None
            return

        self._motion_state = "opening" if target_position > current else "closing"
        self._motion_target_position = target_position

    def _clear_motion_state_if_settled(self) -> None:
        """Clear the optimistic motion state when coordinator data has settled."""
        current = self.module.current_position
        target = self.module.target_position

        if current is not None and target is not None and current != target:
            self._motion_state = None
            self._motion_target_position = None
            return

        if (
            self._motion_target_position is not None
            and current is not None
            and current == self._motion_target_position
        ):
            self._motion_state = None
            self._motion_target_position = None

    def _handle_coordinator_update(self) -> None:
        """Update the entity when fresh data arrives."""
        self._clear_motion_state_if_settled()
        super()._handle_coordinator_update()

    async def _async_run_command(
        self,
        command: Callable[..., Any],
        *args: Any,
        target_position: int | None = None,
    ) -> None:
        """Run a pyatmo command and refresh coordinator data."""
        try:
            await command(*args)
        except ApiError as err:
            raise HomeAssistantError(str(err)) from err
        self._set_motion_state(target_position)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
