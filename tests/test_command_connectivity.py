"""Gateway errors reach the coordinator through each command transport."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import HomeAssistantError
from pyatmo.const import SETSTATE_ENDPOINT
from velux_active.api import VeluxActiveCannotConnect, VeluxActiveClient
from velux_active.const import CONF_HASH_SIGN_KEY, CONF_SIGN_KEY_ID
from velux_active.coordinator import VeluxActiveDataUpdateCoordinator
from velux_active.lock import VeluxDepartureLock


@pytest.fixture
def context():
    raw = {"status": "ok", "body": {"errors": [{"code": 6, "id": "gateway1"}]}}
    response = SimpleNamespace(
        ok=True,
        status=200,
        headers={"content-type": "application/json"},
        json=AsyncMock(return_value=raw),
        text=AsyncMock(side_effect=lambda: json.dumps(raw)),
        read=AsyncMock(side_effect=lambda: json.dumps(raw).encode()),
    )
    pending = AsyncMock()
    pending.__aenter__.return_value = response
    session = SimpleNamespace(
        post=Mock(return_value=pending), request=Mock(return_value=pending)
    )
    client = VeluxActiveClient(session, "user@example.com", "password")
    client._auth.async_get_access_token = AsyncMock(return_value="token")
    hass = SimpleNamespace(session=session, config=SimpleNamespace(time_zone="UTC"))
    coordinator = VeluxActiveDataUpdateCoordinator(
        hass,
        SimpleNamespace(
            data={
                CONF_HASH_SIGN_KEY: "AAAAAAAAAAAAAAAAAAAAAA==",
                CONF_SIGN_KEY_ID: "key",
            }
        ),
        client,
    )
    coordinator.hass = hass  # The HA base stub only stores keyword arguments.
    coordinator.data = SimpleNamespace(
        gateway_connectivity={"gateway1": True, "gateway2": True},
        homes={
            "home1": SimpleNamespace(
                entity_id="home1", modules={"gateway1": SimpleNamespace(locked=False)}
            )
        },
    )
    coordinator.async_update_listeners = Mock()
    coordinator.async_request_refresh = AsyncMock()
    coordinator.start_fast_polling = Mock()
    coordinator.last_update_success = False
    return coordinator, raw


def assert_connectivity(coordinator, code):
    assert coordinator.data.gateway_connectivity == {
        "gateway1": code != 6,
        "gateway2": True,
    }
    assert coordinator.async_update_listeners.call_count == (1 if code == 6 else 0)
    assert coordinator.last_update_success is False


@pytest.mark.parametrize("code", [6, 9])
@pytest.mark.parametrize("action", ["lock", "unlock", "setconfigs"])
async def test_command_errors_update_only_the_affected_gateway(context, code, action):
    coordinator, raw = context
    raw["body"]["errors"][0]["code"] = code
    lock = VeluxDepartureLock(coordinator, "home1", "gateway1")
    lock.async_write_ha_state = Mock()

    if action == "setconfigs":
        with pytest.raises(VeluxActiveCannotConnect):
            await coordinator.client.async_set_controlled_openers(
                "home1", "sensor1", "gateway1", "external_covers"
            )
        assert coordinator.client._controlled_openers_by_home == {}
    else:
        with pytest.raises(HomeAssistantError, match=f"Departure {action} errors"):
            await getattr(lock, f"async_{action}")()
        assert lock.is_locked is False
        lock.async_write_ha_state.assert_not_called()

    assert_connectivity(coordinator, code)
    coordinator.start_fast_polling.assert_not_called()
    coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.parametrize("code", [6, 9])
async def test_pyatmo_body_errors_update_connectivity_and_preserve_response(
    context, code
):
    coordinator, raw = context
    raw["body"]["errors"][0]["code"] = code

    response = await coordinator.client._auth.async_post_api_request(SETSTATE_ENDPOINT)

    assert await response.json() is raw
    assert_connectivity(coordinator, code)


def test_response_before_coordinator_or_first_poll_is_safe(context):
    coordinator, raw = context
    client = VeluxActiveClient(SimpleNamespace(), "user@example.com", "password")
    client.handle_command_response(raw)
    coordinator.data = None
    coordinator.client.handle_command_response(raw)
    coordinator.async_update_listeners.assert_not_called()
