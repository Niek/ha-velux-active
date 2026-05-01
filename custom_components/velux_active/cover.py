"""Cover platform for Velux Active with Netatmo."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import time
from collections.abc import Callable
from typing import Any
import logging

import aiohttp
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

from .const import (
    CONF_HASH_SIGN_KEY,
    CONF_SIGN_KEY_ID,
    VELUX_API_URL,
    VELUX_APP_TYPE,
    VELUX_APP_VERSION,
)
from .coordinator import VeluxActiveConfigEntry
from .entity import VeluxActiveEntity

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Window module identification
# ---------------------------------------------------------------------------
# The Velux API uses the same module type (NXO) for both roller shutters and
# roof windows. Windows are identified by their module name containing a
# keyword from _WINDOW_NAME_KEYWORDS (e.g. "Window", "Fenetre").
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
    1. Module ID is in the explicit allow-list WINDOW_MODULE_IDS.
    2. The module's name (from the API) contains a window-related keyword.
    """
    if module_id in WINDOW_MODULE_IDS:
        return True
    name = getattr(module, "name", "") or ""
    return any(kw in name.lower() for kw in _WINDOW_NAME_KEYWORDS)


def _compute_hash(
    hash_sign_key_b64: str,
    position: int,
    timestamp: int,
    nonce: int,
    device_id: str,
) -> str:
    """Compute the HMAC-SHA512 hash required to sign a window position command.

    The Velux API requires signed commands for roof windows as a security
    measure (windows are openings in the roof). The signature proves the
    command originated from a paired device.

    Formula:
        msg  = f"target_position{position}{timestamp}{nonce}{device_id}"
        hash = HMAC-SHA512(key=base64decode(HashSignKey), msg=msg)
        result = base64encode(hash).replace('+', '-').replace('/', '_')
    """
    string_to_hash = f"target_position{position}{timestamp}{nonce}{device_id}"
    key = base64.b64decode(hash_sign_key_b64)
    digest = hmac.new(key, string_to_hash.encode("utf-8"), hashlib.sha512).digest()
    result = base64.b64encode(digest).decode("utf-8")
    return result.replace("+", "-").replace("/", "_")


# ---------------------------------------------------------------------------
# Batch command manager
# ---------------------------------------------------------------------------

class _BatchCommandManager:
    """Collect signed window commands and send them in a single setstate call.

    When HA calls open_cover on a group, it calls each entity's open_cover
    sequentially. We collect all commands that arrive within a short window
    (150 ms) and then send them all in one request with incrementing nonces,
    matching the behaviour of the official Velux app.

    This is important because the Velux API uses the nonce to prevent replay
    attacks — if two commands share the same timestamp and nonce, the second
    is rejected with error code 28.
    """

    def __init__(self) -> None:
        self._pending: list[dict] = []
        self._task: asyncio.Task | None = None
        self._home_id: str | None = None
        self._bridge_id: str | None = None
        self._access_token_getter = None
        self._hash_sign_key: str = ""
        self._sign_key_id: str = ""
        self._timezone: str = "UTC"

    def setup(
        self,
        home_id: str,
        bridge_id: str,
        access_token_getter,
        hash_sign_key: str,
        sign_key_id: str,
        timezone: str,
    ) -> None:
        self._home_id = home_id
        self._bridge_id = bridge_id
        self._access_token_getter = access_token_getter
        self._hash_sign_key = hash_sign_key
        self._sign_key_id = sign_key_id
        self._timezone = timezone

    def queue(self, module_id: str, raw_position: int) -> asyncio.Future:
        """Queue a window command. Returns a Future resolved when the batch fires."""
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending.append({"id": module_id, "position": raw_position, "future": future})

        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._fire_after_delay())

        return future

    async def _fire_after_delay(self) -> None:
        """Wait briefly for more commands to arrive, then send them all at once."""
        await asyncio.sleep(0.5)
        await self._send_batch()

    async def _send_batch(self) -> None:
        if not self._pending:
            return

        # Deduplicate by module ID — keep only the latest command per window
        # This handles double-tap scenarios where the same window is commanded twice
        seen: dict[str, dict] = {}
        for cmd in self._pending:
            seen[cmd["id"]] = cmd
        commands = list(seen.values())
        self._pending.clear()

        timestamp = int(time.time())
        access_token = await self._access_token_getter()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        modules = []
        for nonce, cmd in enumerate(commands):
            position_hash = _compute_hash(
                self._hash_sign_key,
                cmd["position"],
                timestamp,
                nonce,
                cmd["id"],
            )
            modules.append({
                "id": cmd["id"],
                "nonce": nonce,
                "bridge": self._bridge_id,
                "sign_key_id": self._sign_key_id,
                "target_position": cmd["position"],
                "hash_target_position": position_hash,
                "timestamp": timestamp,
            })

        payload = {
            "app_type": VELUX_APP_TYPE,
            "app_version": VELUX_APP_VERSION,
            "home": {
                "id": self._home_id,
                "timezone": self._timezone,
                "modules": modules,
            },
        }

        _LOGGER.debug(
            "Sending batched setstate for %d window(s) at timestamp %d",
            len(modules),
            timestamp,
        )

        error: Exception | None = None
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{VELUX_API_URL}/syncapi/v1/setstate",
                    json=payload,
                    headers=headers,
                ) as response:
                    # Handle empty responses (e.g. rate limit or auth failure)
                    text = await response.text()
                    if not text.strip():
                        error = HomeAssistantError(
                            f"Empty response from API (status {response.status}) "
                            "— possibly rate limited or token expired"
                        )
                    elif not response.ok:
                        error = HomeAssistantError(f"Signed setstate failed: {text}")
                    else:
                        import json as _json
                        result = _json.loads(text)
                        api_errors = result.get("body", {}).get("errors", [])
                        if api_errors:
                            error = HomeAssistantError(f"Signed setstate errors: {api_errors}")
        except Exception as err:
            error = err

        for cmd in commands:
            future = cmd["future"]
            if not future.done():
                if error:
                    future.set_exception(error)
                else:
                    future.set_result(None)


# One batch manager per config entry (shared across all window entities)
_batch_managers: dict[str, _BatchCommandManager] = {}


def _get_batch_manager(entry_id: str) -> _BatchCommandManager:
    if entry_id not in _batch_managers:
        _batch_managers[entry_id] = _BatchCommandManager()
    return _batch_managers[entry_id]


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

    def _get_home(self):
        """Return the pyatmo Home object that owns this module."""
        for home in self.coordinator.client._account.homes.values():
            if self._module_id in home.modules:
                return home
        return None

    def _get_bridge_id(self) -> str | None:
        """Return the NXG gateway module ID."""
        for home in self.coordinator.client._account.homes.values():
            for module_id, module in home.modules.items():
                if type(module).__name__ == "NXG":
                    return module_id
        return None

    def _get_timezone(self) -> str:
        """Return the HA configured timezone string."""
        return str(self.coordinator.hass.config.time_zone)

    async def _move_to_ha_position(self, ha_position: int) -> None:
        raw_position = self._ha_to_raw_position(ha_position)
        if self._is_window_device and self._signing_enabled:
            home = self._get_home()
            bridge_id = self._get_bridge_id()
            if home is None or bridge_id is None:
                raise HomeAssistantError(
                    "Could not find home or gateway for signed window command"
                )

            batch = _get_batch_manager(self.coordinator.config_entry.entry_id)
            batch.setup(
                home_id=home.entity_id,
                bridge_id=bridge_id,
                access_token_getter=self.coordinator.client._auth.async_get_access_token,
                hash_sign_key=self._hash_sign_key,
                sign_key_id=self._sign_key_id,
                timezone=self._get_timezone(),
            )
            await batch.queue(self._module_id, raw_position)
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
        bridge_id = self._get_bridge_id()
        if home is None or bridge_id is None:
            raise HomeAssistantError(
                "Could not find home or gateway for stop command"
            )

        payload = {
            "app_type": VELUX_APP_TYPE,
            "app_version": VELUX_APP_VERSION,
            "home": {
                "id": home.entity_id,
                "timezone": self._get_timezone(),
                "modules": [{"id": bridge_id, "stop_movements": "all"}],
            },
        }

        access_token = await self.coordinator.client._auth.async_get_access_token()
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{VELUX_API_URL}/syncapi/v1/setstate",
                json=payload,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            ) as response:
                result = await response.json(content_type=None)
                if not response.ok:
                    raise HomeAssistantError(f"Stop command failed: {result}")

        self._motion_state = None
        self._motion_target_position = None
        self.coordinator.start_fast_polling()
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    def _motion_direction(self) -> str | None:
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
        try:
            await command(*args)
        except ApiError as err:
            raise HomeAssistantError(str(err)) from err
        self._set_motion_state(target_position)
        self.coordinator.start_fast_polling()
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()