"""Cover command failures must reach HA without reporting successful movement."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest
from homeassistant.exceptions import HomeAssistantError
from pyatmo.exceptions import ApiError
from velux_active.api import VeluxActiveCannotConnect, VeluxActiveInvalidAuth
from velux_active.cover import VeluxActiveCover


@pytest.fixture
def cover():
    module = SimpleNamespace(
        entity_id="shutter1",
        name="Rolluik",
        velux_type="shutter",
        bridge=None,
        current_position=25,
        target_position=25,
        reachable=True,
        async_set_target_position=AsyncMock(return_value=True),
        async_stop=AsyncMock(return_value=True),
    )
    coordinator = SimpleNamespace(
        config_entry=SimpleNamespace(data={}),
        data=SimpleNamespace(covers={module.entity_id: module}),
        start_fast_polling=Mock(),
        async_request_refresh=AsyncMock(),
    )
    entity = VeluxActiveCover(coordinator, module.entity_id)
    entity.async_write_ha_state = Mock()
    return entity


def assert_no_command_effects(cover):
    assert cover._motion_state is None
    assert cover._motion_target_position is None
    cover.coordinator.start_fast_polling.assert_not_called()
    cover.async_write_ha_state.assert_not_called()
    cover.coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (ApiError("503 Service Unavailable"), "503 Service Unavailable"),
        (ApiError(), "VELUX API request failed"),
        (TimeoutError(), "timed out waiting for the VELUX service"),
        (aiohttp.ClientConnectionError("connection lost"), "connection lost"),
        (aiohttp.ClientError(), "could not connect to the VELUX service"),
        (
            VeluxActiveCannotConnect("Authentication failed with 503"),
            "Authentication failed with 503",
        ),
        (VeluxActiveCannotConnect(), "could not connect to the VELUX service"),
        (
            VeluxActiveInvalidAuth("invalid_grant"),
            "VELUX authentication failed; reauthenticate the integration",
        ),
    ],
)
async def test_expected_failure_raises_contextual_ha_error(cover, error, reason):
    cover.module.async_set_target_position.side_effect = error

    with pytest.raises(HomeAssistantError) as raised:
        await cover.async_set_cover_position(position=75)

    assert str(raised.value) == (
        f"Could not set cover position to 75% for Rolluik: {reason}"
    )
    assert raised.value.__cause__ is error
    cover.module.async_set_target_position.assert_awaited_once_with(75)
    assert_no_command_effects(cover)


async def test_rejected_command_raises_without_optimistic_motion(cover, caplog):
    cover.module.async_set_target_position.return_value = False

    with pytest.raises(HomeAssistantError, match="VELUX did not accept the command"):
        await cover.async_open_cover()

    assert "cover command was not accepted" in caplog.text
    assert "module_id=shutter1" in caplog.text
    assert_no_command_effects(cover)
    assert cover.is_opening is False


async def test_rejected_stop_preserves_existing_motion(cover):
    cover._set_motion_state(75)
    cover.module.async_stop.return_value = False

    with pytest.raises(HomeAssistantError, match="Could not stop cover for Rolluik"):
        await cover.async_stop_cover()

    assert cover._motion_state == "opening"
    assert cover._motion_target_position == 75
    cover.coordinator.start_fast_polling.assert_not_called()
    cover.async_write_ha_state.assert_not_called()
    cover.coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.parametrize("error", [RuntimeError("bug"), asyncio.CancelledError()])
async def test_unexpected_errors_and_cancellation_propagate(cover, error):
    cover.module.async_set_target_position.side_effect = error

    with pytest.raises(type(error)) as raised:
        await cover.async_set_cover_position(position=75)

    assert raised.value is error
    assert_no_command_effects(cover)


async def test_accepted_position_command_starts_motion_and_refreshes(cover):
    await cover.async_set_cover_position(position=75)

    assert cover.is_opening is True
    cover.module.async_set_target_position.assert_awaited_once_with(75)
    cover.coordinator.start_fast_polling.assert_called_once_with()
    cover.async_write_ha_state.assert_called_once_with()
    cover.coordinator.async_request_refresh.assert_awaited_once_with()


async def test_accepted_stop_clears_optimistic_motion(cover):
    cover._set_motion_state(75)

    await cover.async_stop_cover()

    assert cover.is_opening is False
    assert cover._motion_target_position is None
    cover.module.async_stop.assert_awaited_once_with()
    cover.coordinator.async_request_refresh.assert_awaited_once_with()
