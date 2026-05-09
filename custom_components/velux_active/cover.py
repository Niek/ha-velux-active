"""Cover platform for Velux Active with Netatmo."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
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

WINDOW_MODULE_IDS: set[str] = set()
_WINDOW_NAME_KEYWORDS = ("window", "fenetre", "fenêtre", "raam", "fenster", "finestra")


def _module_is_window(module_id: str, module: Any) -> bool:
    """Return True if this module is a roof window rather than a shutter."""
    if str(getattr(module, "velux_type", "") or "").lower() == "window":
        return True
    if module_id in WINDOW_MODULE_IDS:
        return True
    name = getattr(module, "name", "") or ""
    return any(keyword in name.lower() for keyword in _WINDOW_NAME_KEYWORDS)


def _decode_hash_sign_key(hash_sign_key_b64: str) -> bytes:
    """Decode a Hash Sign Key in standard or URL-safe Base64 form."""
    value = hash_sign_key_b64.strip().replace("-", "+").replace("_", "/")
    value += "=" * (-len(value) % 4)
    return base64.b64decode(value, validate=True)


def _compute_hash(
    hash_sign_key_b64: str,
    position: int,
    timestamp: int,
    nonce: int,
    device_id: str,
) -> str:
    """Compute the HMAC-SHA512 hash required for a window position command."""
    string_to_hash = f"target_position{position}{timestamp}{nonce}{device_id}"
    key = _decode_hash_sign_key(hash_sign_key_b64)
    digest = hmac.new(key, string_to_hash.encode("utf-8"), hashlib.sha512).digest()
    result = base64.b64encode(digest).decode("utf-8")
    return result.replace("+", "-").replace("/", "_")


class _BatchCommandManager:
    """Collect signed window commands and send them in one setstate call."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._pending: list[dict[str, Any]] = []
        self._task: asyncio.Task | None = None
        self._home_id: str | None = None
        self._bridge_id: str | None = None
        self._access_token_getter = None
        self._hash_sign_key = ""
        self._sign_key_id = ""
        self._timezone = "UTC"

    def setup(
        self,
        home_id: str,
        bridge_id: str,
        access_token_getter,
        hash_sign_key: str,
        sign_key_id: str,
        timezone: str,
    ) -> None:
        """Set the API context for the next batch."""
        self._home_id = home_id
        self._bridge_id = bridge_id
        self._access_token_getter = access_token_getter
        self._hash_sign_key = hash_sign_key
        self._sign_key_id = sign_key_id
        self._timezone = timezone

    def queue(self, module_id: str, raw_position: int) -> asyncio.Future:
        """Queue a window command."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending.append({"id": module_id, "position": raw_position, "future": future})

        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._fire_after_delay())

        return future

    async def _fire_after_delay(self) -> None:
        """Wait briefly for grouped commands, then send the batch."""
        await asyncio.sleep(0.5)
        await self._send_batch()

    async def _send_batch(self) -> None:
        if not self._pending:
            return

        seen: dict[str, dict[str, Any]] = {}
        for command in self._pending:
            seen[command["id"]] = command
        commands = list(seen.values())
        self._pending.clear()

        timestamp = int(time.time())
        access_token = await self._access_token_getter()
        modules = []
        for nonce, command in enumerate(commands):
            modules.append(
                {
                    "id": command["id"],
                    "nonce": nonce,
                    "bridge": self._bridge_id,
                    "sign_key_id": self._sign_key_id,
                    "target_position": command["position"],
                    "hash_target_position": _compute_hash(
                        self._hash_sign_key,
                        command["position"],
                        timestamp,
                        nonce,
                        command["id"],
                    ),
                    "timestamp": timestamp,
                }
            )

        payload = {
            "app_type": VELUX_APP_TYPE,
            "app_version": VELUX_APP_VERSION,
            "home": {
                "id": self._home_id,
                "timezone": self._timezone,
                "modules": modules,
            },
        }

        error: Exception | None = None
        try:
            session = async_get_clientsession(self._hass)
            async with session.post(
                f"{VELUX_API_URL}/syncapi/v1/setstate",
                json=payload,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            ) as response:
                text = await response.text()
                if not text.strip():
                    error = HomeAssistantError(
                        f"Empty response from signed setstate (status {response.status})"
                    )
                elif not response.ok:
                    error = HomeAssistantError(f"Signed setstate failed: {text}")
                else:
                    result = json.loads(text)
                    api_errors = result.get("body", {}).get("errors", [])
                    if api_errors:
                        _LOGGER.debug(
                            "Signed setstate API errors: status=%s errors=%s body=%s",
                            response.status,
                            api_errors,
                            text[:1000],
                        )
                        error = HomeAssistantError(f"Signed setstate errors: {api_errors}")
        except Exception as err:
            _LOGGER.exception("Signed setstate request failed")
            error = err

        for command in commands:
            future = command["future"]
            if not future.done():
                if error:
                    future.set_exception(error)
                else:
                    future.set_result(None)


_batch_managers: dict[str, _BatchCommandManager] = {}


def _get_batch_manager(entry_id: str, hass: HomeAssistant) -> _BatchCommandManager:
    """Return the batch manager for a config entry."""
    if entry_id not in _batch_managers:
        _batch_managers[entry_id] = _BatchCommandManager(hass)
    return _batch_managers[entry_id]


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
        self._is_window_device = _module_is_window(module_id, self.module)

        entry_data = coordinator.config_entry.data
        entry_options = coordinator.config_entry.options
        self._hash_sign_key = entry_options.get(
            CONF_HASH_SIGN_KEY,
            entry_data.get(CONF_HASH_SIGN_KEY, ""),
        ).strip()
        self._sign_key_id = entry_options.get(
            CONF_SIGN_KEY_ID,
            entry_data.get(CONF_SIGN_KEY_ID, ""),
        ).strip()
        self._signing_enabled = bool(self._hash_sign_key and self._sign_key_id)

        _LOGGER.debug(
            "Cover entity created: id=%s name=%r velux_type=%r is_window=%s signing=%s",
            module_id,
            getattr(self.module, "name", "?"),
            getattr(self.module, "velux_type", None),
            self._is_window_device,
            self._signing_enabled,
        )

    @property
    def device_class(self) -> CoverDeviceClass:
        """Return the cover device class."""
        return CoverDeviceClass.WINDOW if self._is_window_device else CoverDeviceClass.SHUTTER

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
        if self._is_window_device and self._signing_enabled:
            await self._move_to_ha_position(100)
        else:
            await self._async_run_command(self.module.async_open, target_position=100)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        if self._is_window_device and self._signing_enabled:
            await self._move_to_ha_position(0)
        else:
            await self._async_run_command(self.module.async_close, target_position=0)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        if self._is_window_device and self._signing_enabled:
            await self._async_stop_via_bridge()
        else:
            await self._async_run_command(self.module.async_stop, target_position=None)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover to a position."""
        await self._move_to_ha_position(kwargs[ATTR_POSITION])

    async def _move_to_ha_position(self, position: int) -> None:
        """Move the cover to a Home Assistant position."""
        if self._is_window_device and self._signing_enabled:
            home = self._get_home()
            bridge_id = self._get_bridge_id()
            if home is None or bridge_id is None:
                raise HomeAssistantError(
                    "Could not find home or gateway for signed window command"
                )

            batch = _get_batch_manager(
                self.coordinator.config_entry.entry_id,
                self.coordinator.hass,
            )
            batch.setup(
                home_id=home.entity_id,
                bridge_id=bridge_id,
                access_token_getter=self.coordinator.client._auth.async_get_access_token,
                hash_sign_key=self._hash_sign_key,
                sign_key_id=self._sign_key_id,
                timezone=str(self.coordinator.hass.config.time_zone),
            )
            await batch.queue(self._module_id, position)
            self._set_motion_state(position)
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()
            return

        await self._async_run_command(
            self.module.async_set_target_position,
            position,
            target_position=position,
        )

    async def _async_stop_via_bridge(self) -> None:
        """Stop all cover movements by sending stop_movements to the gateway."""
        home = self._get_home()
        bridge_id = self._get_bridge_id()
        if home is None or bridge_id is None:
            raise HomeAssistantError("Could not find home or gateway for stop command")

        payload = {
            "app_type": VELUX_APP_TYPE,
            "app_version": VELUX_APP_VERSION,
            "home": {
                "id": home.entity_id,
                "timezone": str(self.coordinator.hass.config.time_zone),
                "modules": [{"id": bridge_id, "stop_movements": "all"}],
            },
        }
        access_token = await self.coordinator.client._auth.async_get_access_token()
        session = async_get_clientsession(self.coordinator.hass)
        async with session.post(
            f"{VELUX_API_URL}/syncapi/v1/setstate",
            json=payload,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
        ) as response:
            text = await response.text()
            if not text.strip() or not response.ok:
                raise HomeAssistantError(
                    f"Stop command failed (status {response.status}): "
                    f"{text[:200] or 'empty response'}"
                )
            result = json.loads(text)
            api_errors = result.get("body", {}).get("errors", [])
            if api_errors:
                raise HomeAssistantError(f"Stop command errors: {api_errors}")

        self._motion_state = None
        self._motion_target_position = None
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

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

    def _motion_direction(self) -> str | None:
        """Return the current movement direction from live or optimistic data."""
        current = self.module.current_position
        target = self._motion_target_position
        if target is None:
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
