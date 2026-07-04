"""Base entity for Velux Active with Netatmo."""

from __future__ import annotations

import json

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONTROL_URL,
    DOMAIN,
    MANUFACTURER,
    VELUX_API_URL,
    VELUX_APP_TYPE,
    VELUX_APP_VERSION,
)
from .coordinator import VeluxActiveDataUpdateCoordinator
from .signing import resolve_bridge_id


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

    # ------------------------------------------------------------------
    # Gateway / setstate helpers shared by the control platforms
    # ------------------------------------------------------------------

    def _get_home(self):
        """Return the pyatmo Home object that owns this module."""
        for home in self.coordinator.client._account.homes.values():
            if self._module_id in home.modules:
                return home
        return None

    def _get_bridge_id(self, home) -> str | None:
        """Return the gateway module ID for this module's owning home.

        Prefer the module's own ``bridge`` link so accounts with multiple
        gateways route commands to the correct one, only falling back to a
        home-wide lookup when that home has exactly one gateway.
        """
        nxg_ids = [
            module_id
            for module_id, module in home.modules.items()
            if type(module).__name__ == "NXG"
        ]
        return resolve_bridge_id(getattr(self.module, "bridge", None), nxg_ids)

    def _get_timezone(self) -> str:
        """Return the HA configured timezone string."""
        return str(self.coordinator.hass.config.time_zone)

    async def _async_setstate(
        self, home, modules: list[dict], *, action: str = "Command"
    ) -> None:
        """POST a setstate request for this entity's coordinator/home."""
        await async_post_setstate(
            self.coordinator.hass,
            self.coordinator.client,
            home.entity_id,
            self._get_timezone(),
            modules,
            action=action,
        )


async def async_post_setstate(
    hass,
    client,
    home_id: str,
    timezone: str,
    modules: list[dict],
    *,
    action: str = "Command",
) -> None:
    """POST a setstate request and raise HomeAssistantError on any failure.

    Shared by the cover, switch and lock platforms; signed and unsigned
    commands differ only in the per-module fields the caller supplies.
    """
    payload = {
        "app_type": VELUX_APP_TYPE,
        "app_version": VELUX_APP_VERSION,
        "home": {"id": home_id, "timezone": timezone, "modules": modules},
    }
    access_token = await client._auth.async_get_access_token()
    session = async_get_clientsession(hass)
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
            raise HomeAssistantError(
                f"{action} returned empty response (status {response.status})"
            )
        result = json.loads(text)
        if not response.ok:
            raise HomeAssistantError(f"{action} failed: {result}")
        api_errors = result.get("body", {}).get("errors", [])
        if api_errors:
            raise HomeAssistantError(f"{action} errors: {api_errors}")
