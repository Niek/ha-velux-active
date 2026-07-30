"""Tests for VELUX diagnostic sensors."""

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory
from velux_active import sensor


class FakeData:
    def __init__(self):
        self.rooms = {}
        self.sensor_modules = {
            "sensor1": {
                "id": "sensor1",
                "type": "NXS",
                "name": "Indoor sensor",
                "battery_level": 3794,
                "battery_percent": 46,
                "battery_state": "medium",
                "rf_strength": 59,
                "rf_state": "full",
            }
        }
        self.gateway_status = {
            "gateway1": {
                "id": "gateway1",
                "type": "NXG",
                "wifi_strength": 47,
                "wifi_state": "full",
            }
        }


class FakeCoordinator:
    def __init__(self):
        self.data = FakeData()
        self.listeners = []

    def async_add_listener(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)


class FakeEntry:
    def __init__(self):
        self.runtime_data = FakeCoordinator()

    def async_on_unload(self, remove_listener):
        return None


async def test_setup_adds_default_disabled_diagnostic_sensors():
    entities = []

    await sensor.async_setup_entry(None, FakeEntry(), entities.extend)

    assert len(entities) == 7
    diagnostic_entities = [
        entity
        for entity in entities
        if isinstance(
            entity,
            (
                sensor.VeluxModuleDiagnosticSensor,
                sensor.VeluxGatewayDiagnosticSensor,
            ),
        )
    ]
    assert len(diagnostic_entities) == 6
    assert all(
        entity.entity_description.entity_category == EntityCategory.DIAGNOSTIC
        and entity.entity_description.entity_registry_enabled_default is False
        for entity in diagnostic_entities
    )


async def test_diagnostic_sensor_values_and_enum_options():
    entities = []
    entry = FakeEntry()
    await sensor.async_setup_entry(None, entry, entities.extend)
    entities_by_id = {entity._attr_unique_id: entity for entity in entities}

    assert entities_by_id["sensor1_battery"].native_value == 46
    assert entities_by_id["sensor1_battery_voltage"].native_value == 3.794
    assert entities_by_id["sensor1_battery_state"].native_value == "medium"
    assert entities_by_id["sensor1_rf_signal_strength"].native_value == -59
    assert entities_by_id["sensor1_rf_signal_quality"].native_value == "full"
    assert entities_by_id["gateway1_wifi_signal_strength"].native_value == -47
    assert entities_by_id["gateway1_wifi_signal_quality"].native_value == "full"

    assert (
        entities_by_id["sensor1_battery_voltage"].entity_description.device_class
        == SensorDeviceClass.VOLTAGE
    )
    assert (
        entities_by_id["sensor1_rf_signal_strength"].entity_description.device_class
        == SensorDeviceClass.SIGNAL_STRENGTH
    )
    assert entities_by_id["sensor1_battery_state"].entity_description.options == [
        "full",
        "high",
        "medium",
        "low",
        "very_low",
    ]
    assert entities_by_id[
        "gateway1_wifi_signal_quality"
    ].entity_description.options == [
        "full",
        "high",
        "medium",
        "low",
    ]

    entry.runtime_data.data.sensor_modules["sensor1"]["battery_state"] = ""
    entry.runtime_data.data.sensor_modules["sensor1"]["rf_state"] = ""
    entry.runtime_data.data.gateway_status["gateway1"]["wifi_state"] = "unexpected"
    entry.runtime_data.data.gateway_status["gateway1"]["wifi_strength"] = -47
    assert entities_by_id["sensor1_battery_state"].native_value is None
    assert entities_by_id["sensor1_rf_signal_quality"].native_value is None
    assert entities_by_id["gateway1_wifi_signal_quality"].native_value is None
    assert entities_by_id["gateway1_wifi_signal_strength"].native_value == -47


async def test_diagnostic_sensors_are_discovered_once_and_track_availability():
    entities = []
    entry = FakeEntry()
    entry.runtime_data.data.sensor_modules["sensor1"] = {
        "id": "sensor1",
        "type": "NXS",
    }
    entry.runtime_data.data.gateway_status["gateway1"] = {
        "id": "gateway1",
        "type": "NXG",
    }

    await sensor.async_setup_entry(None, entry, entities.extend)
    assert entities == []

    entry.runtime_data.data.sensor_modules["sensor1"]["battery_state"] = "medium"
    entry.runtime_data.data.gateway_status["gateway1"]["wifi_state"] = "full"
    entry.runtime_data.listeners[0]()

    assert {entity._attr_unique_id for entity in entities} == {
        "sensor1_battery_state",
        "gateway1_wifi_signal_quality",
    }

    entry.runtime_data.listeners[0]()
    assert len(entities) == 2

    battery_state = next(
        entity
        for entity in entities
        if entity._attr_unique_id == "sensor1_battery_state"
    )
    del entry.runtime_data.data.sensor_modules["sensor1"]["battery_state"]
    assert battery_state.available is False

    entry.runtime_data.data.sensor_modules["sensor1"]["battery_state"] = "high"
    assert battery_state.available is True
    assert battery_state.native_value == "high"
