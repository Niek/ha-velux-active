"""Tests for VELUX window switches."""

from unittest.mock import AsyncMock, Mock

from velux_active import switch


class FakeModule:
    def __init__(self, module_id, velux_type, *, silent=None):
        self.entity_id = module_id
        self.name = module_id
        self.velux_type = velux_type
        self.silent = silent
        self.mode = "manual"
        self.bridge = "gateway1"
        self.room_id = None
        self.firmware_revision = 1


class NXG:
    pass


class FakeHome:
    def __init__(self, modules):
        self.entity_id = "home1"
        self.modules = modules
        self.rooms = {}


class FakeData:
    def __init__(self):
        self.covers = {
            "blind1": FakeModule("blind1", "blind", silent=False),
            "window1": FakeModule("window1", "window", silent=False),
            "window2": FakeModule("window2", "window"),
        }
        self.homes = {
            "home1": FakeHome({"gateway1": NXG(), **self.covers}),
        }


class FakeCoordinator:
    def __init__(self):
        self.data = FakeData()
        self.async_request_refresh = AsyncMock()


class FakeEntry:
    def __init__(self):
        self.runtime_data = FakeCoordinator()


async def test_setup_adds_silent_switch_only_for_capable_windows():
    entities = []

    await switch.async_setup_entry(None, FakeEntry(), entities.extend)

    assert {entity._attr_unique_id for entity in entities} == {
        "window1_auto_ventilation",
        "window1_silent_operation",
        "window2_auto_ventilation",
    }


def test_silent_switch_uses_reported_module_state():
    coordinator = FakeCoordinator()
    entity = switch.VeluxSilentModeSwitch(coordinator, "window1")

    assert entity.is_on is False
    coordinator.data.covers["window1"].silent = True
    assert entity.is_on is True
    coordinator.data.covers["window1"].silent = None
    assert entity.is_on is None


async def test_silent_switch_sends_unsigned_setstate_and_refreshes():
    coordinator = FakeCoordinator()
    entity = switch.VeluxSilentModeSwitch(coordinator, "window1")
    entity._async_setstate = AsyncMock()
    entity.async_write_ha_state = Mock()
    home = coordinator.data.homes["home1"]

    await entity.async_turn_on()

    entity._async_setstate.assert_awaited_once_with(
        home,
        [{"id": "window1", "bridge": "gateway1", "silent": True}],
        action="Silent operation command",
    )
    entity.async_write_ha_state.assert_called_once_with()
    coordinator.async_request_refresh.assert_awaited_once_with()

    entity._async_setstate.reset_mock()
    entity.async_write_ha_state.reset_mock()
    coordinator.async_request_refresh.reset_mock()

    await entity.async_turn_off()

    entity._async_setstate.assert_awaited_once_with(
        home,
        [{"id": "window1", "bridge": "gateway1", "silent": False}],
        action="Silent operation command",
    )
    entity.async_write_ha_state.assert_called_once_with()
    coordinator.async_request_refresh.assert_awaited_once_with()
