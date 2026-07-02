"""Cover platform for Velux Active with Netatmo."""

from __future__ import annotations

import logging
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
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .batch import BatchCommandError, get_batch_manager
from .const import (
    CONF_HASH_SIGN_KEY,
    CONF_SIGN_KEY_GATEWAY_ID,
    CONF_SIGN_KEY_ID,
)
from .coordinator import VeluxActiveConfigEntry
from .entity import VeluxActiveEntity

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Window module identification
# ---------------------------------------------------------------------------
# The Velux API uses the same module type (NXO) for both roller shutters and
# roof windows. Prefer the API velux_type field when pyatmo exposes it, then
# fall back to the manual allow-list and legacy name-keyword detection.
#
# If your window names don't match, you can add your module IDs to
# WINDOW_MODULE_IDS below. Find them in the HA debug logs by enabling debug
# logging for custom_components.velux_active.
# ---------------------------------------------------------------------------
WINDOW_MODULE_IDS: set[str] = set()

_WINDOW_NAME_KEYWORDS = ("window", "fenetre", "fenêtre", "raam", "fenster", "finestra")


def _module_is_window(module_id: str, module: Any) -> bool:
    """Return True if this module is a roof window rather than a shutter.

    Checks (in order):
    1. The API velux_type field identifies this as a window.
    2. Module ID is in the explicit allow-list WINDOW_MODULE_IDS.
    3. The module's name (from the API) contains a window-related keyword.
    """
    velux_type = str(getattr(module, "velux_type", "") or "").lower()
    if velux_type == "window":
        return True
    if module_id in WINDOW_MODULE_IDS:
        return True
    name = getattr(module, "name", "") or ""
    return any(kw in name.lower() for kw in _WINDOW_NAME_KEYWORDS)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeluxActiveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities = [
        VeluxActiveCover(coordinator, module_id)
        for module_id in sorted(coordinator.data.covers)
    ]
    async_add_entities(entities)


class VeluxActiveCover(VeluxActiveEntity, CoverEntity):
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    def __init__(self, coordinator, module_id: str) -> None:
        super().__init__(coordinator, module_id)
        self._motion_state: str | None = None
        self._motion_target_position: int | None = None
        self._is_window_device: bool = _module_is_window(module_id, self.module)

        entry_data = coordinator.config_entry.data
        self._hash_sign_key: str = entry_data.get(CONF_HASH_SIGN_KEY, "").strip()
        self._sign_key_id: str = entry_data.get(CONF_SIGN_KEY_ID, "").strip()
        self._sign_key_gateway_id: str = entry_data.get(
            CONF_SIGN_KEY_GATEWAY_ID, ""
        ).strip()
        self._signing_enabled: bool = bool(self._hash_sign_key and self._sign_key_id)

        _LOGGER.debug(
            "Cover entity created: id=%s name=%r is_window=%s signing=%s",
            module_id,
            getattr(self.module, "name", "?"),
            self._is_window_device,
            self._signing_enabled,
        )

    # ------------------------------------------------------------------
    # Position helpers
    # ------------------------------------------------------------------

    def _raw_to_ha_position(self, raw: int | None) -> int | None:
        """Convert API position to HA position (0 = closed, 100 = open)."""
        return raw

    def _ha_to_raw_position(self, position: int) -> int:
        """Convert HA position to API position."""
        return position

    # ------------------------------------------------------------------
    # CoverEntity properties
    # ------------------------------------------------------------------

    @property
    def device_class(self) -> CoverDeviceClass:
        return CoverDeviceClass.WINDOW if self._is_window_device else CoverDeviceClass.SHUTTER

    @property
    def current_cover_position(self) -> int | None:
        return self._raw_to_ha_position(self.module.current_position)

    @property
    def is_closed(self) -> bool | None:
        position = self.current_cover_position
        return None if position is None else position == 0

    @property
    def is_opening(self) -> bool | None:
        return self._motion_direction() == "opening"

    @property
    def is_closing(self) -> bool | None:
        return self._motion_direction() == "closing"

    # ------------------------------------------------------------------
    # CoverEntity actions
    # ------------------------------------------------------------------

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._move_to_ha_position(100)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._move_to_ha_position(0)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        if self._is_window_device and self._signing_enabled:
            await self._async_stop_via_bridge()
        else:
            await self._async_run_command(self.module.async_stop, target_position=None)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        await self._move_to_ha_position(kwargs[ATTR_POSITION])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _move_to_ha_position(self, ha_position: int) -> None:
        raw_position = self._ha_to_raw_position(ha_position)
        if self._is_window_device and self._signing_enabled:
            home = self._get_home()
            bridge_id = self._get_bridge_id(home) if home is not None else None
            if home is None or bridge_id is None:
                raise HomeAssistantError(
                    "Could not find home or gateway for signed window command"
                )
            # Only one gateway's key is stored; refuse windows on another gateway
            # rather than sign (and batch) them with the wrong key.
            if self._sign_key_gateway_id and bridge_id != self._sign_key_gateway_id:
                raise HomeAssistantError(
                    f"Window is on gateway {bridge_id}, but signing keys were "
                    f"paired with gateway {self._sign_key_gateway_id}. Re-pair "
                    "with this window's gateway to control it."
                )

            batch = get_batch_manager(self.coordinator.config_entry.entry_id)
            batch.setup(
                home_id=home.entity_id,
                bridge_id=bridge_id,
                session=async_get_clientsession(self.coordinator.hass),
                access_token_getter=self.coordinator.client._auth.async_get_access_token,
                hash_sign_key=self._hash_sign_key,
                sign_key_id=self._sign_key_id,
                timezone=self._get_timezone(),
            )
            try:
                await batch.queue(self._module_id, raw_position)
            except BatchCommandError as err:
                raise HomeAssistantError(str(err)) from err
            self._set_motion_state(ha_position)
            self.coordinator.start_fast_polling()
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()
        else:
            await self._async_run_command(
                self.module.async_set_target_position,
                raw_position,
                target_position=ha_position,
            )

    async def _async_stop_via_bridge(self) -> None:
        """Stop all cover movements by sending stop_movements to the gateway.

        This command targets the bridge (NXG gateway) rather than individual
        windows, and does not require signing. It stops all in-progress
        movements immediately.
        """
        home = self._get_home()
        bridge_id = self._get_bridge_id(home) if home is not None else None
        if home is None or bridge_id is None:
            raise HomeAssistantError(
                "Could not find home or gateway for stop command"
            )

        await self._async_setstate(
            home,
            [{"id": bridge_id, "stop_movements": "all"}],
            action="Stop command",
        )

        self._motion_state = None
        self._motion_target_position = None
        self.coordinator.start_fast_polling()
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    def _motion_direction(self) -> str | None:
        current = self.module.current_position
        target = self.module.target_position
        if current is not None and target is not None and current != target:
            return "opening" if target > current else "closing"

        current = self.current_cover_position
        target = self._motion_target_position
        if current is not None and target is not None and current != target:
            return "opening" if target > current else "closing"
        return self._motion_state

    def _set_motion_state(self, target_position: int | None) -> None:
        current = self.current_cover_position
        if target_position is None or current is None or target_position == current:
            self._motion_state = None
            self._motion_target_position = None
            return
        self._motion_state = "opening" if target_position > current else "closing"
        self._motion_target_position = target_position

    def _clear_motion_state_if_settled(self) -> None:
        current = self.module.current_position
        target = self.module.target_position

        if current is not None and target is not None and current != target:
            self._motion_state = None
            self._motion_target_position = None
            return

        current = self.current_cover_position
        if (
            self._motion_target_position is not None
            and current is not None
            and current == self._motion_target_position
        ):
            self._motion_state = None
            self._motion_target_position = None

    def _handle_coordinator_update(self) -> None:
        self._clear_motion_state_if_settled()
        super()._handle_coordinator_update()

    async def _async_run_command(
        self,
        command: Callable[..., Any],
        *args: Any,
        target_position: int | None = None,
    ) -> None:
        """Run a pyatmo command and refresh coordinator data."""
        module = self.module
        try:
            accepted = await command(*args)
        except ApiError as err:
            raise HomeAssistantError(str(err)) from err
        if accepted is False:
            gateway = module.home.modules.get(module.bridge) if module.bridge else None
            _LOGGER.warning(
                (
                    "VELUX Active cover command was not accepted: command=%s "
                    "module_id=%s name=%s bridge=%s velux_type=%s "
                    "current_position=%s target_position=%s requested_position=%s "
                    "reachable=%s mode=%s gateway_locked=%s gateway_secure=%s "
                    "gateway_busy=%s gateway_calibrating=%s gateway_is_raining=%s "
                    "gateway_pincode_enabled=%s"
                ),
                getattr(command, "__name__", type(command).__name__),
                module.entity_id,
                module.name,
                module.bridge,
                getattr(module, "velux_type", None),
                module.current_position,
                module.target_position,
                target_position,
                module.reachable,
                getattr(module, "mode", None),
                getattr(gateway, "locked", None),
                getattr(gateway, "secure", None),
                getattr(gateway, "busy", None),
                getattr(gateway, "calibrating", None),
                getattr(gateway, "is_raining", None),
                getattr(gateway, "pincode_enabled", None),
            )
        self._set_motion_state(target_position)
        self.coordinator.start_fast_polling()
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
