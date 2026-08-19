"""Button platform for Velux Active with Netatmo."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONTROL_URL, DOMAIN, MANUFACTURER
from .coordinator import VeluxActiveConfigEntry, VeluxActiveDataUpdateCoordinator
from .entity import async_post_setstate, gateway_device_info

MODULE_MODELS = {
    "NXO": "Opening",
    "NXS": "Indoor Climate Sensor",
    "NXD": "Departure Switch",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeluxActiveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up identify buttons for supported VELUX modules."""
    coordinator = entry.runtime_data
    known_modules: set[tuple[str, str]] = set()

    @callback
    def _async_add_new_entities() -> None:
        entities: list[ButtonEntity] = []
        for home_id, module_id, module_type in _identifiable_modules(coordinator):
            module_key = (home_id, module_id)
            if module_key in known_modules:
                continue
            entities.append(
                VeluxIdentifyButton(coordinator, home_id, module_id, module_type)
            )
            if module_type == "NXG":
                entities.append(
                    VeluxStopAllMovementsButton(coordinator, home_id, module_id)
                )
            known_modules.add(module_key)

        if entities:
            async_add_entities(entities)

    _async_add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_entities))


def _identifiable_modules(
    coordinator: VeluxActiveDataUpdateCoordinator,
) -> Iterator[tuple[str, str, str]]:
    """Yield modules for which the APK exposes the identify action."""
    for home_id, home in sorted(coordinator.data.homes.items()):
        for module_id, module in sorted(home.modules.items()):
            module_type = _identifiable_module_type(coordinator, module_id, module)
            if module_type is not None:
                yield home_id, module_id, module_type


def _identifiable_module_type(
    coordinator: VeluxActiveDataUpdateCoordinator,
    module_id: str,
    module: Any,
) -> str | None:
    """Return the supported VELUX module type, if known."""
    if type(module).__name__ == "NXG":
        return "NXG"
    if module_id in coordinator.data.covers:
        return "NXO"

    module_type = coordinator.data.sensor_modules.get(module_id, {}).get("type")
    return module_type if module_type in {"NXS", "NXD"} else None


class VeluxIdentifyButton(
    CoordinatorEntity[VeluxActiveDataUpdateCoordinator], ButtonEntity
):
    """Button that asks a VELUX module to identify itself."""

    _attr_has_entity_name = True
    _attr_name = "Identify"
    _attr_device_class = ButtonDeviceClass.IDENTIFY
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: VeluxActiveDataUpdateCoordinator,
        home_id: str,
        module_id: str,
        module_type: str,
    ) -> None:
        """Initialize the identify button."""
        super().__init__(coordinator)
        self._home_id = home_id
        self._module_id = module_id
        self._module_type = module_type
        self._attr_unique_id = f"{module_id}_identify"

    @property
    def _module(self) -> Any | None:
        """Return the current pyatmo module."""
        home = self.coordinator.data.homes.get(self._home_id)
        return home.modules.get(self._module_id) if home is not None else None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for the physical module."""
        if self._module_type == "NXG":
            return gateway_device_info(self._module_id)

        module = self._module
        raw_module = self.coordinator.data.sensor_modules.get(self._module_id, {})
        model = MODULE_MODELS[self._module_type]
        if self._module_type == "NXO" and module is not None:
            model = (
                (getattr(module, "velux_type", None) or model).replace("_", " ").title()
            )

        return DeviceInfo(
            configuration_url=CONTROL_URL,
            identifiers={(DOMAIN, self._module_id)},
            manufacturer=MANUFACTURER,
            model=model,
            name=getattr(module, "name", None)
            or raw_module.get("name")
            or self._module_id,
            sw_version=str(getattr(module, "firmware_revision", "")) or None,
        )

    @property
    def available(self) -> bool:
        """Return True while the module remains in the home topology."""
        return super().available and self._module is not None

    async def async_press(self) -> None:
        """Send the identify command found in the official VELUX app."""
        home = self.coordinator.data.homes[self._home_id]
        module = home.modules[self._module_id]
        payload = {"id": self._module_id, "identify": True}
        if bridge_id := getattr(module, "bridge", None):
            payload["bridge"] = bridge_id

        await async_post_setstate(
            self.coordinator.hass,
            self.coordinator.client,
            home.entity_id,
            str(self.coordinator.hass.config.time_zone),
            [payload],
            action="Identify command",
        )


class VeluxStopAllMovementsButton(
    CoordinatorEntity[VeluxActiveDataUpdateCoordinator], ButtonEntity
):
    """Button that stops all movements through a VELUX gateway."""

    _attr_has_entity_name = True
    _attr_name = "Stop all movements"

    def __init__(
        self,
        coordinator: VeluxActiveDataUpdateCoordinator,
        home_id: str,
        gateway_id: str,
    ) -> None:
        """Initialize the stop-all button."""
        super().__init__(coordinator)
        self._home_id = home_id
        self._gateway_id = gateway_id
        self._attr_unique_id = f"{gateway_id}_stop_all_movements"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for the gateway."""
        return gateway_device_info(self._gateway_id)

    @property
    def available(self) -> bool:
        """Return True while the gateway remains in the home topology."""
        home = self.coordinator.data.homes.get(self._home_id)
        return (
            super().available and home is not None and self._gateway_id in home.modules
        )

    async def async_press(self) -> None:
        """Stop all movements through the gateway."""
        home = self.coordinator.data.homes[self._home_id]
        await async_post_setstate(
            self.coordinator.hass,
            self.coordinator.client,
            home.entity_id,
            str(self.coordinator.hass.config.time_zone),
            [{"id": self._gateway_id, "stop_movements": "all"}],
            action="Stop all movements command",
        )
        sequences = self.coordinator.gateway_stop_sequences
        sequences[self._gateway_id] = sequences.get(self._gateway_id, 0) + 1
        await self.coordinator.async_request_refresh()
