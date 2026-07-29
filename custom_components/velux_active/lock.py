"""Lock platform for Velux Active with Netatmo.

Exposes each VELUX gateway's departure mode (away/home scenario) as a HA lock.
When locked (away), the gateway disables all window and blind movement — handy
for alarm integrations.

Locking (away):   unsigned command, works without signing keys.
Unlocking (home): HMAC-SHA512 signed, same key as window commands.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_HASH_SIGN_KEY,
    CONF_SIGN_KEY_GATEWAY_ID,
    CONF_SIGN_KEY_ID,
)
from .coordinator import VeluxActiveConfigEntry, VeluxActiveDataUpdateCoordinator
from .entity import async_post_setstate, gateway_device_info
from .signing import allocate_nonces, compute_scenario_hash

_LOGGER = logging.getLogger(__name__)


def _iter_gateways(coordinator: VeluxActiveDataUpdateCoordinator):
    """Yield (home_id, bridge_id) for every NXG gateway across all homes."""
    for home in coordinator.data.homes.values():
        for module_id, module in home.modules.items():
            if type(module).__name__ == "NXG":
                yield home.entity_id, module_id


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeluxActiveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one departure-mode lock per VELUX gateway."""
    coordinator = entry.runtime_data
    async_add_entities(
        VeluxDepartureLock(coordinator, home_id, bridge_id)
        for home_id, bridge_id in _iter_gateways(coordinator)
    )


class VeluxDepartureLock(
    CoordinatorEntity[VeluxActiveDataUpdateCoordinator], LockEntity
):
    """A VELUX gateway's departure mode as a HA lock.

    Locked   = away (all movement disabled)
    Unlocked = home (normal operation)
    """

    _attr_has_entity_name = True
    _attr_name = "Departure Mode"

    def __init__(
        self,
        coordinator: VeluxActiveDataUpdateCoordinator,
        home_id: str,
        bridge_id: str,
    ) -> None:
        """Initialise the lock for one gateway."""
        super().__init__(coordinator)
        self._home_id = home_id
        self._bridge_id = bridge_id
        # Scope the unique id to the gateway so multiple gateways / entries
        # never collide.
        self._attr_unique_id = f"{bridge_id}_departure_lock"

        entry_data = coordinator.config_entry.data
        self._hash_sign_key: str = entry_data.get(CONF_HASH_SIGN_KEY, "").strip()
        self._sign_key_id: str = entry_data.get(CONF_SIGN_KEY_ID, "").strip()
        self._sign_key_gateway_id: str = entry_data.get(
            CONF_SIGN_KEY_GATEWAY_ID, ""
        ).strip()
        self._signing_enabled: bool = bool(self._hash_sign_key and self._sign_key_id)
        # Match the app's signer: advance the nonce when the timestamp repeats
        # so two unlocks in the same second never reuse a (timestamp, nonce) pair.
        self._last_ts: int = 0
        self._last_nonce: int = -1

    @property
    def device_info(self) -> DeviceInfo:
        """Attach to the gateway device."""
        return gateway_device_info(self._bridge_id)

    def _bridge_module(self):
        """Return the pyatmo NXG module for this lock's gateway, or None."""
        for home in self.coordinator.data.homes.values():
            if home.entity_id == self._home_id:
                return home.modules.get(self._bridge_id)
        return None

    @property
    def is_locked(self) -> bool | None:
        """Return departure-mode state from the gateway, None if unavailable."""
        bridge = self._bridge_module()
        if bridge is None:
            return None
        locked = getattr(bridge, "locked", None)
        return None if locked is None else bool(locked)

    async def async_lock(self, **kwargs: Any) -> None:
        """Activate departure mode (away). No signing required."""
        await self._async_scenario(
            [{"id": self._bridge_id, "scenario": "away"}], "lock"
        )

    async def async_unlock(self, **kwargs: Any) -> None:
        """Deactivate departure mode (home). Requires signing keys."""
        if not self._signing_enabled:
            raise HomeAssistantError(
                "Cannot unlock departure mode: signing keys (Hash Sign Key + "
                "Sign Key ID) are not configured. Locking works without them, "
                "but unlocking requires signing."
            )
        if self._sign_key_gateway_id and self._bridge_id != self._sign_key_gateway_id:
            raise HomeAssistantError(
                f"Signing keys were paired with gateway {self._sign_key_gateway_id}, "
                f"not {self._bridge_id}; re-pair to unlock this gateway."
            )

        timestamp, nonce = allocate_nonces(
            int(time.time()), self._last_ts, self._last_nonce
        )
        self._last_ts, self._last_nonce = timestamp, nonce
        module = {
            "id": self._bridge_id,
            "nonce": nonce,
            "sign_key_id": self._sign_key_id,
            "scenario": "home",
            "timestamp": timestamp,
            "hash_scenario": compute_scenario_hash(
                self._hash_sign_key, "home", timestamp, nonce, self._bridge_id
            ),
        }
        await self._async_scenario([module], "unlock")

    async def _async_scenario(self, modules: list[dict], action: str) -> None:
        """Send a scenario setstate command and refresh."""
        await async_post_setstate(
            self.coordinator.hass,
            self.coordinator.client,
            self._home_id,
            str(self.coordinator.hass.config.time_zone),
            modules,
            action=f"Departure {action}",
        )
        # The gateway applies the scenario with a delay; poll quickly so the
        # new state is reflected instead of waiting a full update interval.
        self.coordinator.start_fast_polling()
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
