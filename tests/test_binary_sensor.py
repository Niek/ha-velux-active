"""Tests for VELUX gateway binary sensors."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import EntityCategory
from velux_active import binary_sensor


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


async def test_setup_registers_both_sensors_for_each_gateway():
    entities = []

    def add_entities(new_entities):
        entities.extend(new_entities)

    await binary_sensor.async_setup_entry(None, FakeEntry(), add_entities)

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


def test_gateway_sensors_share_device_metadata():
    coordinator = FakeCoordinator()
    connectivity = binary_sensor.VeluxGatewayConnectivityBinarySensor(
        coordinator, "gateway1"
    )
    rain = binary_sensor.VeluxGatewayRainBinarySensor(coordinator, "gateway1")

    assert connectivity.device_info == rain.device_info
    assert connectivity.device_info["identifiers"] == {("velux_active", "gateway1")}
