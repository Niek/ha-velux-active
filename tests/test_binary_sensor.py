"""Standalone checks for VELUX gateway binary sensors.

Run: python3 tests/test_binary_sensor.py
"""

import asyncio
import importlib.util
import sys
import types
from pathlib import Path


class CoordinatorEntity:
    @classmethod
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, coordinator):
        self.coordinator = coordinator

    @property
    def available(self):
        return True


class BinarySensorEntity:
    pass


class BinarySensorDeviceClass:
    CONNECTIVITY = "connectivity"
    MOISTURE = "moisture"


class EntityCategory:
    DIAGNOSTIC = "diagnostic"


class DeviceInfo(dict):
    def __init__(self, **kwargs):
        super().__init__(kwargs)


def _load_binary_sensor_module():
    modules = {
        "homeassistant": types.ModuleType("homeassistant"),
        "homeassistant.components": types.ModuleType("homeassistant.components"),
        "homeassistant.components.binary_sensor": types.ModuleType(
            "homeassistant.components.binary_sensor"
        ),
        "homeassistant.const": types.ModuleType("homeassistant.const"),
        "homeassistant.core": types.ModuleType("homeassistant.core"),
        "homeassistant.helpers": types.ModuleType("homeassistant.helpers"),
        "homeassistant.helpers.device_registry": types.ModuleType(
            "homeassistant.helpers.device_registry"
        ),
        "homeassistant.helpers.entity_platform": types.ModuleType(
            "homeassistant.helpers.entity_platform"
        ),
        "homeassistant.helpers.update_coordinator": types.ModuleType(
            "homeassistant.helpers.update_coordinator"
        ),
    }
    modules[
        "homeassistant.components.binary_sensor"
    ].BinarySensorDeviceClass = BinarySensorDeviceClass
    modules[
        "homeassistant.components.binary_sensor"
    ].BinarySensorEntity = BinarySensorEntity
    modules["homeassistant.const"].EntityCategory = EntityCategory
    modules["homeassistant.core"].HomeAssistant = object
    modules["homeassistant.helpers.device_registry"].DeviceInfo = DeviceInfo
    modules[
        "homeassistant.helpers.entity_platform"
    ].AddConfigEntryEntitiesCallback = object
    modules[
        "homeassistant.helpers.update_coordinator"
    ].CoordinatorEntity = CoordinatorEntity
    sys.modules.update(modules)

    package_path = (
        Path(__file__).resolve().parents[1] / "custom_components" / "velux_active"
    )
    package = types.ModuleType("velux_active")
    package.__path__ = [str(package_path)]
    sys.modules["velux_active"] = package

    const = types.ModuleType("velux_active.const")
    const.CONTROL_URL = "https://example.invalid"
    const.DOMAIN = "velux_active"
    const.MANUFACTURER = "VELUX"
    sys.modules["velux_active.const"] = const

    coordinator = types.ModuleType("velux_active.coordinator")
    coordinator.VeluxActiveConfigEntry = object
    coordinator.VeluxActiveDataUpdateCoordinator = object
    sys.modules["velux_active.coordinator"] = coordinator

    spec = importlib.util.spec_from_file_location(
        "velux_active.binary_sensor", package_path / "binary_sensor.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


binary_sensor = _load_binary_sensor_module()


class FakeGateway:
    def __init__(self, is_raining):
        self.is_raining = is_raining


class FakeHome:
    def __init__(self, modules):
        self.modules = modules


class FakeData:
    def __init__(self):
        self.gateway_connectivity = {"gateway1": True, "gateway2": False}
        self.homes = {
            "home1": FakeHome(
                {
                    "gateway1": FakeGateway(True),
                    "gateway2": FakeGateway(False),
                }
            )
        }


class FakeCoordinator:
    def __init__(self):
        self.data = FakeData()


class FakeEntry:
    def __init__(self):
        self.runtime_data = FakeCoordinator()


def test_setup_registers_both_sensors_for_each_gateway():
    entities = []

    def add_entities(new_entities):
        entities.extend(new_entities)

    asyncio.run(binary_sensor.async_setup_entry(None, FakeEntry(), add_entities))

    assert len(entities) == 4
    rain_entities = [
        entity
        for entity in entities
        if isinstance(entity, binary_sensor.VeluxGatewayRainBinarySensor)
    ]
    assert {entity._attr_unique_id for entity in rain_entities} == {
        "gateway1_rain",
        "gateway2_rain",
    }
    assert all(
        entity._attr_device_class == BinarySensorDeviceClass.MOISTURE
        and entity._attr_entity_category == EntityCategory.DIAGNOSTIC
        and entity._attr_entity_registry_enabled_default is False
        for entity in rain_entities
    )


def test_rain_sensor_uses_pyatmo_state_and_gateway_availability():
    coordinator = FakeCoordinator()
    sensor = binary_sensor.VeluxGatewayRainBinarySensor(coordinator, "gateway1")

    assert sensor.is_on is True
    assert sensor.available

    gateway = coordinator.data.homes["home1"].modules["gateway1"]
    gateway.is_raining = False
    assert sensor.is_on is False
    assert sensor.available

    gateway.is_raining = None
    assert sensor.is_on is None
    assert not sensor.available

    gateway.is_raining = True
    coordinator.data.gateway_connectivity["gateway1"] = False
    assert sensor.is_on is True
    assert not sensor.available


if __name__ == "__main__":
    for name, function in sorted(globals().items()):
        if name.startswith("test_"):
            function()
            print(f"ok {name}")
    print("all passed")
