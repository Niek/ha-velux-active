"""Lock platform for Velux Active with Netatmo.

Exposes the VELUX gateway's departure mode (away/home scenario) as a HA
lock entity. When locked, all window and blind movement is disabled by the
gateway — useful for integrating with a home alarm system.

Locking (away):   does not require signing.
Unlocking (home): requires HMAC-SHA512 signing, same key as window commands.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from typing import Any

import aiohttp

from homeassistant.components.lock import LockEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_HASH_SIGN_KEY,
    CONF_SIGN_KEY_ID,
    CONTROL_URL,
    DOMAIN,
    MANUFACTURER,
    VELUX_API_URL,
    VELUX_APP_TYPE,
    VELUX_APP_VERSION,
)
from .coordinator import VeluxActiveConfigEntry, VeluxActiveDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


def _compute_scenario_hash(
    hash_sign_key_b64: str,
    scenario: str,
    timestamp: int,
    nonce: int,
    bridge_id: str,
) -> str:
    """Compute HMAC-SHA512 hash for a scenario command.

    Formula:
        msg    = f"scenario{scenario}{timestamp}{nonce}{bridge_id}"
        hash   = HMAC-SHA512(key=base64decode(HashSignKey), msg=msg)
        result = base64encode(hash).replace('+', '-').replace('/', '_')
    """
    string_to_hash = f"scenario{scenario}{timestamp}{nonce}{bridge_id}"
    key = base64.b64decode(hash_sign_key_b64)
    digest = hmac.new(key, string_to_hash.encode("utf-8"), hashlib.sha512).digest()
    result = base64.b64encode(digest).decode("utf-8")
    return result.replace("+", "-").replace("/", "_")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeluxActiveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Velux departure mode lock entity."""
    coordinator = entry.runtime_data
    async_add_entities([VeluxDepartureLock(coordinator)])


class VeluxDepartureLock(CoordinatorEntity[VeluxActiveDataUpdateCoordinator], LockEntity):
    """Represents the VELUX gateway departure mode as a HA lock.

    Locked   = away mode (all movement disabled)
    Unlocked = home mode (normal operation)
    """

    _attr_has_entity_name = True
    _attr_name = "Departure Mode"

    def __init__(self, coordinator: VeluxActiveDataUpdateCoordinator) -> None:
        """Initialise the lock entity."""
        super().__init__(coordinator)

        entry_data = coordinator.config_entry.data
        self._hash_sign_key: str = entry_data.get(CONF_HASH_SIGN_KEY, "").strip()
        self._sign_key_id: str = entry_data.get(CONF_SIGN_KEY_ID, "").strip()
        self._signing_enabled: bool = bool(self._hash_sign_key and self._sign_key_id)

        self._attr_unique_id = f"{DOMAIN}_departure_lock"

    # ------------------------------------------------------------------
    # Device info — attach to the gateway device
    # ------------------------------------------------------------------

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info — attach to the NXG gateway device."""
        bridge_id = self._get_bridge_id()
        connections = (
            {(CONNECTION_NETWORK_MAC, bridge_id)} if bridge_id else set()
        )
        return DeviceInfo(
            configuration_url=CONTROL_URL,
            connections=connections,
            identifiers={(DOMAIN, bridge_id or "velux_gateway")},
            manufacturer=MANUFACTURER,
            model="VELUX Gateway",
            name="VELUX Gateway",
        )

    # ------------------------------------------------------------------
    # LockEntity properties
    # ------------------------------------------------------------------

    @property
    def is_locked(self) -> bool:
        """Return True when departure mode is active.

        Read from the NXG gateway's 'locked' field in the homestatus response.
        This is the true state from the API — no optimistic tracking needed.
        """
        bridge = self._get_bridge_module()
        if bridge is not None:
            return bool(getattr(bridge, "locked", False))
        return False  # fallback if bridge not found

    @property
    def available(self) -> bool:
        """Return True if signing keys are configured (required for unlock)."""
        return self._signing_enabled

    # ------------------------------------------------------------------
    # LockEntity actions
    # ------------------------------------------------------------------

    async def async_lock(self, **kwargs: Any) -> None:
        """Activate departure mode (away). No signing required."""
        home_id, bridge_id = self._get_home_and_bridge()

        payload = {
            "app_type": VELUX_APP_TYPE,
            "app_version": VELUX_APP_VERSION,
            "home": {
                "id": home_id,
                "timezone": str(self.coordinator.hass.config.time_zone),
                "modules": [{"id": bridge_id, "scenario": "away"}],
            },
        }

        await self._async_post_setstate(payload)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_unlock(self, **kwargs: Any) -> None:
        """Deactivate departure mode (home). Requires signing."""
        if not self._signing_enabled:
            raise HomeAssistantError(
                "Cannot unlock: Hash Sign Key and Sign Key ID are not configured. "
                "See the integration documentation for instructions."
            )

        home_id, bridge_id = self._get_home_and_bridge()
        timestamp = int(time.time())
        nonce = 0
        scenario_hash = _compute_scenario_hash(
            self._hash_sign_key,
            "home",
            timestamp,
            nonce,
            bridge_id,
        )

        payload = {
            "app_type": VELUX_APP_TYPE,
            "app_version": VELUX_APP_VERSION,
            "home": {
                "id": home_id,
                "timezone": str(self.coordinator.hass.config.time_zone),
                "modules": [
                    {
                        "id": bridge_id,
                        "nonce": nonce,
                        "sign_key_id": self._sign_key_id,
                        "scenario": "home",
                        "timestamp": timestamp,
                        "hash_scenario": scenario_hash,
                    }
                ],
            },
        }

        await self._async_post_setstate(payload)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_bridge_module(self):
        """Return the NXG gateway module object, or None."""
        for home in self.coordinator.client._account.homes.values():
            for module_id, module in home.modules.items():
                if type(module).__name__ == "NXG":
                    return module
        return None

    def _get_bridge_id(self) -> str | None:
        """Return the NXG gateway module ID."""
        module = self._get_bridge_module()
        if module is not None:
            return module.entity_id
        return None

    def _get_home_and_bridge(self) -> tuple[str, str]:
        """Return (home_id, bridge_id) or raise if not found."""
        for home in self.coordinator.client._account.homes.values():
            for module_id, module in home.modules.items():
                if type(module).__name__ == "NXG":
                    return home.entity_id, module_id
        raise HomeAssistantError("Could not find VELUX gateway")

    async def _async_post_setstate(self, payload: dict) -> None:
        """POST a setstate payload to the Velux API."""
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
                text = await response.text()
                if not text.strip() or not response.ok:
                    raise HomeAssistantError(
                        f"API error (status {response.status}): {text[:200] or 'empty response'}"
                    )
                import json as _json
                result = _json.loads(text)
                if False:  # placeholder
                    raise HomeAssistantError(f"Departure mode command failed: {result}")
                errors = result.get("body", {}).get("errors", [])
                if errors:
                    raise HomeAssistantError(f"Departure mode errors: {errors}")