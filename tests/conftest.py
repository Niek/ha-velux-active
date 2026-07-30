"""Shared lightweight imports for tests that do not need Home Assistant itself."""

import sys
import types
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class Stub:
    """Minimal generic and callable Home Assistant stub."""

    @classmethod
    def __class_getitem__(cls, item: Any):
        return cls

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)

    def __call__(self, value: Any) -> Any:
        return value


class CoordinatorEntity(Stub):
    def __init__(self, coordinator: Any) -> None:
        self.coordinator = coordinator

    @property
    def available(self) -> bool:
        return True


class FlowBase:
    def async_show_form(self, **kwargs: Any) -> dict[str, Any]:
        return {"type": "form", **kwargs}

    def async_create_entry(self, **kwargs: Any) -> dict[str, Any]:
        return {"type": "create_entry", **kwargs}


class ConfigFlow(FlowBase):
    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__()


class OptionsFlow(FlowBase):
    pass


class DeviceInfo(dict):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(kwargs)


class UpdateFailed(Exception):
    pass


class ConfigEntryAuthFailed(Exception):
    pass


class HomeAssistantError(Exception):
    pass


@dataclass(frozen=True, kw_only=True)
class SensorEntityDescription:
    key: str
    name: str | None = None
    device_class: str | None = None
    native_unit_of_measurement: str | None = None
    options: list[str] | None = None
    state_class: str | None = None
    entity_category: str | None = None
    entity_registry_enabled_default: bool = True
    suggested_display_precision: int | None = None


def _install_module(name: str, **attributes: Any) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


for package_name in (
    "homeassistant",
    "homeassistant.components",
    "homeassistant.helpers",
):
    package = _install_module(package_name)
    package.__path__ = []

_install_module(
    "homeassistant.components.binary_sensor",
    BinarySensorDeviceClass=SimpleNamespace(
        CONNECTIVITY="connectivity",
        MOISTURE="moisture",
    ),
    BinarySensorEntity=Stub,
)
_install_module(
    "homeassistant.components.cover",
    ATTR_POSITION="position",
    CoverDeviceClass=SimpleNamespace(SHUTTER="shutter", WINDOW="window"),
    CoverEntity=Stub,
    CoverEntityFeature=SimpleNamespace(OPEN=1, CLOSE=2, STOP=4, SET_POSITION=8),
)
_install_module(
    "homeassistant.components.sensor",
    SensorDeviceClass=SimpleNamespace(
        AQI="aqi",
        BATTERY="battery",
        CO2="carbon_dioxide",
        ENUM="enum",
        HUMIDITY="humidity",
        ILLUMINANCE="illuminance",
        SIGNAL_STRENGTH="signal_strength",
        TEMPERATURE="temperature",
        VOLTAGE="voltage",
    ),
    SensorEntity=Stub,
    SensorEntityDescription=SensorEntityDescription,
    SensorStateClass=SimpleNamespace(MEASUREMENT="measurement"),
)
_install_module(
    "homeassistant.components.switch",
    SwitchEntity=Stub,
)
_install_module(
    "homeassistant.config_entries",
    ConfigEntry=Stub,
    ConfigFlow=ConfigFlow,
    ConfigFlowResult=dict,
    OptionsFlow=OptionsFlow,
)
_install_module(
    "homeassistant.const",
    CONCENTRATION_PARTS_PER_MILLION="ppm",
    CONF_PASSWORD="password",
    CONF_USERNAME="username",
    LIGHT_LUX="lx",
    PERCENTAGE="%",
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT="dBm",
    EntityCategory=SimpleNamespace(DIAGNOSTIC="diagnostic"),
    UnitOfElectricPotential=SimpleNamespace(VOLT="V"),
    UnitOfTemperature=SimpleNamespace(CELSIUS="°C"),
)
_install_module(
    "homeassistant.core",
    HomeAssistant=object,
    callback=lambda func: func,
)
_install_module(
    "homeassistant.exceptions",
    ConfigEntryAuthFailed=ConfigEntryAuthFailed,
    HomeAssistantError=HomeAssistantError,
)
_install_module(
    "homeassistant.helpers.aiohttp_client",
    async_get_clientsession=lambda hass: getattr(hass, "session", None),
)
_install_module("homeassistant.helpers.device_registry", DeviceInfo=DeviceInfo)
_install_module(
    "homeassistant.helpers.entity_platform",
    AddConfigEntryEntitiesCallback=object,
)
_install_module(
    "homeassistant.helpers.selector",
    SelectOptionDict=Stub,
    SelectSelector=Stub,
    SelectSelectorConfig=Stub,
    SelectSelectorMode=SimpleNamespace(DROPDOWN="dropdown"),
)
_install_module(
    "homeassistant.helpers.update_coordinator",
    CoordinatorEntity=CoordinatorEntity,
    DataUpdateCoordinator=Stub,
    UpdateFailed=UpdateFailed,
)

package_path = ROOT / "custom_components" / "velux_active"
velux_active = types.ModuleType("velux_active")
velux_active.__path__ = [str(package_path)]
sys.modules["velux_active"] = velux_active
