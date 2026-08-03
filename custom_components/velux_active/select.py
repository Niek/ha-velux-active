"""Select platform for VELUX indoor climate sensor control targets."""

from __future__ import annotations

from typing import Any, ClassVar

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import VeluxActiveCannotConnect
from .const import (
    CONTROL_URL,
    CONTROLLED_OPENERS_OPTIONS,
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import VeluxActiveConfigEntry, VeluxActiveDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeluxActiveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up controlled-product selects for NXS modules."""
    coordinator = entry.runtime_data
    known_modules: set[str] = set()

    @callback
    def _async_add_new_entities() -> None:
        entities: list[SelectEntity] = []
        for module_id in sorted(coordinator.data.controlled_openers):
            module = coordinator.data.sensor_modules.get(module_id, {})
            if module.get("type") != "NXS" or module_id in known_modules:
                continue
            entities.append(VeluxControlledOpenersSelect(coordinator, module_id))
            known_modules.add(module_id)

        if entities:
            async_add_entities(entities)

    _async_add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_entities))


class VeluxControlledOpenersSelect(
    CoordinatorEntity[VeluxActiveDataUpdateCoordinator], SelectEntity
):
    """Select which products an indoor climate sensor controls."""

    _attr_has_entity_name = True
    _attr_name = "Controlled products"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options: ClassVar[list[str]] = list(CONTROLLED_OPENERS_OPTIONS)

    def __init__(
        self,
        coordinator: VeluxActiveDataUpdateCoordinator,
        module_id: str,
    ) -> None:
        """Initialize the controlled-products select."""
        super().__init__(coordinator)
        self._module_id = module_id
        self._attr_unique_id = f"{module_id}_controlled_openers"

    @property
    def _config(self) -> dict[str, str]:
        """Return the current controlled opener configuration."""
        return self.coordinator.data.controlled_openers.get(self._module_id, {})

    @property
    def _module(self) -> dict[str, Any]:
        """Return the current physical module data."""
        return self.coordinator.data.sensor_modules.get(self._module_id, {})

    @property
    def current_option(self) -> str | None:
        """Return the currently configured control target."""
        option = self._config.get("controlled_openers")
        return option if option in CONTROLLED_OPENERS_OPTIONS else None

    @property
    def device_info(self) -> DeviceInfo:
        """Attach the select to the physical indoor climate sensor."""
        module = self._module
        firmware_revision = module.get("firmware_revision")
        return DeviceInfo(
            configuration_url=CONTROL_URL,
            identifiers={(DOMAIN, self._module_id)},
            manufacturer=MANUFACTURER,
            model="Indoor Climate Sensor",
            name=module.get("name") or self._module_id,
            suggested_area=module.get("room_name"),
            sw_version=str(firmware_revision)
            if firmware_revision is not None
            else None,
        )

    @property
    def available(self) -> bool:
        """Return whether the module has a supported current configuration."""
        return (
            super().available
            and self._module.get("type") == "NXS"
            and self.current_option is not None
        )

    async def async_select_option(self, option: str) -> None:
        """Set which products this indoor climate sensor controls."""
        if option not in CONTROLLED_OPENERS_OPTIONS:
            raise HomeAssistantError(f"Unsupported controlled product: {option}")

        config = self._config
        home_id = config.get("home_id")
        bridge_id = config.get("bridge")
        if not home_id or not bridge_id:
            raise HomeAssistantError("Could not find home or gateway")

        try:
            await self.coordinator.client.async_set_controlled_openers(
                home_id,
                self._module_id,
                bridge_id,
                option,
            )
        except VeluxActiveCannotConnect as err:
            raise HomeAssistantError(
                f"Failed to update controlled products: {err}"
            ) from err

        config["controlled_openers"] = option
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
