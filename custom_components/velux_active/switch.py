"""Switch platform for Velux Active with Netatmo.

Exposes the VELUX gateway's automatic ventilation algorithm as a switch.
When enabled, the gateway automatically opens and closes windows based on
indoor CO₂ levels, temperature and humidity from the gateway's sensors.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONTROL_URL,
    DOMAIN,
    MANUFACTURER,
    VELUX_API_URL,
    VELUX_APP_TYPE,
    VELUX_APP_VERSION,
)
from .coordinator import VeluxActiveConfigEntry, VeluxActiveDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# API mode values
MODE_ALGO_ENABLED = "auto"
MODE_ALGO_DISABLED = "algo_disabled"


def _get_bridge(coordinator) -> tuple[str | None, Any]:
    """Return (bridge_id, bridge_module) or (None, None)."""
    for home in coordinator.client._account.homes.values():
        for module_id, module in home.modules.items():
            if type(module).__name__ == "NXG":
                return module_id, module
    return None, None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeluxActiveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Velux algorithm switch."""
    coordinator = entry.runtime_data
    bridge_id, bridge = _get_bridge(coordinator)
    if bridge is not None:
        # Add one switch per window that supports the algorithm
        entities = []
        for module_id, module in coordinator.data.covers.items():
            if getattr(module, "velux_type", "") == "window":
                entities.append(VeluxAlgorithmSwitch(coordinator, module_id))
        if entities:
            async_add_entities(entities)


class VeluxAlgorithmSwitch(CoordinatorEntity[VeluxActiveDataUpdateCoordinator], SwitchEntity):
    """Switch to enable/disable the automatic ventilation algorithm for a window.

    When on, the gateway automatically adjusts the window position based on
    indoor CO₂, temperature and humidity readings.
    When off, the window only moves in response to manual commands.
    """

    _attr_has_entity_name = True
    _attr_name = "Auto Ventilation"
    _attr_icon = "mdi:air-filter"

    def __init__(
        self,
        coordinator: VeluxActiveDataUpdateCoordinator,
        module_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._module_id = module_id
        self._attr_unique_id = f"{module_id}_algorithm"

    @property
    def module(self):
        """Return the pyatmo module object."""
        return self.coordinator.data.covers[self._module_id]

    @property
    def is_on(self) -> bool | None:
        """Return True when auto ventilation algorithm is active."""
        mode = getattr(self.module, "mode", None)
        if mode is None:
            return None
        return mode != MODE_ALGO_DISABLED

    @property
    def device_info(self) -> DeviceInfo:
        model = (getattr(self.module, "velux_type", "cover") or "cover").replace("_", " ").title()
        return DeviceInfo(
            configuration_url=CONTROL_URL,
            identifiers={(DOMAIN, self.module.entity_id)},
            manufacturer=MANUFACTURER,
            model=model,
            name=self.module.name,
            sw_version=str(getattr(self.module, "firmware_revision", "") or ""),
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the automatic ventilation algorithm."""
        await self._async_set_mode(MODE_ALGO_ENABLED)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the automatic ventilation algorithm."""
        await self._async_set_mode(MODE_ALGO_DISABLED)

    async def _async_set_mode(self, mode: str) -> None:
        """Send a mode setstate command for this window."""
        home = self._get_home()
        bridge_id = self._get_bridge_id()
        if home is None or bridge_id is None:
            raise HomeAssistantError("Could not find home or gateway")

        payload = {
            "app_type": VELUX_APP_TYPE,
            "app_version": VELUX_APP_VERSION,
            "home": {
                "id": home.entity_id,
                "timezone": str(self.coordinator.hass.config.time_zone),
                "modules": [
                    {
                        "id": self._module_id,
                        "bridge": bridge_id,
                        "mode": mode,
                    }
                ],
            },
        }

        _LOGGER.debug("Setting algorithm mode %s for %s", mode, self._module_id)

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
                    raise HomeAssistantError(f"Mode command failed: {result}")
                errors = result.get("body", {}).get("errors", [])
                if errors:
                    raise HomeAssistantError(f"Mode command errors: {errors}")

        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    def _get_home(self):
        for home in self.coordinator.client._account.homes.values():
            if self._module_id in home.modules:
                return home
        return None

    def _get_bridge_id(self) -> str | None:
        for home in self.coordinator.client._account.homes.values():
            for module_id, module in home.modules.items():
                if type(module).__name__ == "NXG":
                    return module_id
        return None
