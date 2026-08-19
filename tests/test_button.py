"""Tests for VELUX identify buttons."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from velux_active import button


class NXG:
    def __init__(self):
        self.entity_id = "gateway1"
        self.name = "Gateway"
        self.bridge = None


class FakeModule:
    def __init__(self, module_id, *, bridge="gateway1", velux_type=None):
        self.entity_id = module_id
        self.name = module_id
        self.bridge = bridge
        self.velux_type = velux_type
        self.room_id = None
        self.firmware_revision = 1


class FakeHome:
    def __init__(self, modules):
        self.entity_id = "home1"
        self.modules = modules
        self.rooms = {}


class FakeData:
    def __init__(self):
        self.covers = {
            "window1": FakeModule("window1", velux_type="window"),
        }
        self.sensor_modules = {
            "sensor1": {"id": "sensor1", "type": "NXS", "name": "Sensor"},
            "switch1": {
                "id": "switch1",
                "type": "NXD",
                "name": "Departure switch",
            },
        }
        self.homes = {
            "home1": FakeHome(
                {
                    "gateway1": NXG(),
                    **self.covers,
                    "sensor1": FakeModule("sensor1"),
                    "switch1": FakeModule("switch1"),
                    "unsupported1": FakeModule("unsupported1"),
                }
            )
        }


class FakeCoordinator:
    def __init__(self):
        self.data = FakeData()
        self.hass = SimpleNamespace(config=SimpleNamespace(time_zone="Europe/Berlin"))
        self.client = object()
        self.listeners = []

    def async_add_listener(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)


class FakeEntry:
    def __init__(self):
        self.runtime_data = FakeCoordinator()

    def async_on_unload(self, remove_listener):
        return None


async def test_setup_adds_identify_buttons_for_supported_modules():
    entities = []

    await button.async_setup_entry(None, FakeEntry(), entities.extend)

    assert {entity._attr_unique_id for entity in entities} == {
        "gateway1_identify",
        "gateway1_stop_all_movements",
        "sensor1_identify",
        "switch1_identify",
        "window1_identify",
    }


async def test_sensor_identify_buttons_are_discovered_once():
    entities = []
    entry = FakeEntry()
    entry.runtime_data.data.sensor_modules = {}

    await button.async_setup_entry(None, entry, entities.extend)

    assert {entity._attr_unique_id for entity in entities} == {
        "gateway1_identify",
        "gateway1_stop_all_movements",
        "window1_identify",
    }

    entry.runtime_data.data.sensor_modules["sensor1"] = {
        "id": "sensor1",
        "type": "NXS",
    }
    entry.runtime_data.listeners[0]()
    entry.runtime_data.listeners[0]()

    assert [entity._attr_unique_id for entity in entities].count(
        "sensor1_identify"
    ) == 1


async def test_identify_sends_bridge_for_child_module(monkeypatch):
    coordinator = FakeCoordinator()
    async_post_setstate = AsyncMock()
    monkeypatch.setattr(button, "async_post_setstate", async_post_setstate)
    entity = button.VeluxIdentifyButton(coordinator, "home1", "window1", "NXO")

    await entity.async_press()

    async_post_setstate.assert_awaited_once_with(
        coordinator.hass,
        coordinator.client,
        "home1",
        "Europe/Berlin",
        [{"id": "window1", "bridge": "gateway1", "identify": True}],
        action="Identify command",
    )


async def test_identify_omits_bridge_for_gateway(monkeypatch):
    coordinator = FakeCoordinator()
    async_post_setstate = AsyncMock()
    monkeypatch.setattr(button, "async_post_setstate", async_post_setstate)
    entity = button.VeluxIdentifyButton(coordinator, "home1", "gateway1", "NXG")

    await entity.async_press()

    async_post_setstate.assert_awaited_once_with(
        coordinator.hass,
        coordinator.client,
        "home1",
        "Europe/Berlin",
        [{"id": "gateway1", "identify": True}],
        action="Identify command",
    )


async def test_stop_all_movements_targets_gateway_and_refreshes(monkeypatch):
    coordinator = FakeCoordinator()
    coordinator.async_request_refresh = AsyncMock()
    async_post_setstate = AsyncMock()
    monkeypatch.setattr(button, "async_post_setstate", async_post_setstate)
    entity = button.VeluxStopAllMovementsButton(coordinator, "home1", "gateway1")

    await entity.async_press()

    async_post_setstate.assert_awaited_once_with(
        coordinator.hass,
        coordinator.client,
        "home1",
        "Europe/Berlin",
        [{"id": "gateway1", "stop_movements": "all"}],
        action="Stop all movements command",
    )
    coordinator.async_request_refresh.assert_awaited_once_with()
