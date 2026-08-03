"""Tests for VELUX indoor climate sensor controlled-product selects."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from homeassistant.const import EntityCategory
from velux_active import select


class FakeData:
    def __init__(self):
        self.sensor_modules = {
            "sensor1": {
                "id": "sensor1",
                "type": "NXS",
                "name": "Indoor sensor",
                "room_name": "Bedroom",
            },
            "switch1": {"id": "switch1", "type": "NXD"},
        }
        self.controlled_openers = {
            "sensor1": {
                "home_id": "home1",
                "bridge": "gateway1",
                "controlled_openers": "windows",
            },
            "switch1": {
                "home_id": "home1",
                "bridge": "gateway1",
                "controlled_openers": "windows",
            },
        }


class FakeCoordinator:
    def __init__(self):
        self.data = FakeData()
        self.client = SimpleNamespace(async_set_controlled_openers=AsyncMock())
        self.async_request_refresh = AsyncMock()
        self.listeners = []

    def async_add_listener(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)


class FakeEntry:
    def __init__(self):
        self.runtime_data = FakeCoordinator()

    def async_on_unload(self, remove_listener):
        return None


async def test_setup_adds_select_only_for_nxs_modules():
    entities = []

    await select.async_setup_entry(None, FakeEntry(), entities.extend)

    assert [entity._attr_unique_id for entity in entities] == [
        "sensor1_controlled_openers"
    ]
    assert entities[0]._attr_options == ["windows", "external_covers"]
    assert entities[0]._attr_entity_category == EntityCategory.CONFIG


async def test_select_uses_reported_state_and_writes_confirmed_payload():
    coordinator = FakeCoordinator()
    entity = select.VeluxControlledOpenersSelect(coordinator, "sensor1")
    entity.async_write_ha_state = Mock()

    assert entity.current_option == "windows"
    assert entity.available

    await entity.async_select_option("external_covers")

    coordinator.client.async_set_controlled_openers.assert_awaited_once_with(
        "home1", "sensor1", "gateway1", "external_covers"
    )
    assert entity.current_option == "external_covers"
    entity.async_write_ha_state.assert_called_once_with()
    coordinator.async_request_refresh.assert_awaited_once_with()


async def test_select_is_discovered_once_when_config_appears():
    entities = []
    entry = FakeEntry()
    entry.runtime_data.data.controlled_openers = {}

    await select.async_setup_entry(None, entry, entities.extend)
    assert entities == []

    entry.runtime_data.data.controlled_openers["sensor1"] = {
        "home_id": "home1",
        "bridge": "gateway1",
        "controlled_openers": "windows",
    }
    entry.runtime_data.listeners[0]()
    entry.runtime_data.listeners[0]()

    assert [entity._attr_unique_id for entity in entities] == [
        "sensor1_controlled_openers"
    ]
