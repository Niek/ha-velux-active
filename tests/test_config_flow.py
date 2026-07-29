"""Regression tests for the shared gateway-pairing flow."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from velux_active import config_flow
from velux_active.api import VeluxActiveCannotConnect
from velux_active.pairing import SigningKey

HOMESDATA = {
    "body": {
        "homes": [
            {
                "id": "home1",
                "name": "Home",
                "modules": [{"id": "gateway1", "name": "Gateway", "type": "NXG"}],
            }
        ]
    }
}


async def test_initial_flow_selects_sole_gateway():
    flow = config_flow.VeluxActiveConfigFlow()
    flow._client = SimpleNamespace(
        async_get_raw_homesdata=AsyncMock(return_value=HOMESDATA)
    )
    flow.async_step_pair = AsyncMock(return_value={"step_id": "pair"})

    result = await flow.async_step_select_gateway()

    assert result == {"step_id": "pair"}
    assert flow._pair_home_id == "home1"
    assert flow._pair_gateway_id == "gateway1"


async def test_options_flow_selects_sole_gateway(monkeypatch: pytest.MonkeyPatch):
    client = SimpleNamespace(async_get_raw_homesdata=AsyncMock(return_value=HOMESDATA))
    monkeypatch.setattr(
        config_flow, "VeluxActiveClient", lambda *args, **kwargs: client
    )

    flow = config_flow.VeluxActiveOptionsFlow()
    flow.hass = SimpleNamespace(session=object())
    flow.config_entry = SimpleNamespace(
        data={"username": "user", "password": "pass"},
        options={},
    )
    flow.async_step_pair = AsyncMock(return_value={"step_id": "pair"})

    result = await flow.async_step_select_gateway()

    assert result == {"step_id": "pair"}
    assert flow._pair_home_id == "home1"
    assert flow._pair_gateway_id == "gateway1"


async def test_pairing_connection_error_is_reported():
    flow = config_flow.VeluxActiveConfigFlow()
    flow._client = SimpleNamespace(
        async_get_raw_homesdata=AsyncMock(
            side_effect=VeluxActiveCannotConnect("offline")
        )
    )

    result = await flow.async_step_select_gateway()

    assert result["step_id"] == "select_gateway"
    assert result["errors"] == {"base": "pairing_failed"}


async def test_pair_step_triggers_selected_gateway():
    client = SimpleNamespace(async_trigger_retrieve_key=AsyncMock())
    flow = config_flow.VeluxActiveConfigFlow()
    flow._client = client
    flow._pair_home_id = "home1"
    flow._pair_gateway_id = "gateway1"
    flow.async_step_pair_button = AsyncMock(return_value={"step_id": "pair_button"})

    result = await flow.async_step_pair({"gateway_host": " 192.0.2.1 "})

    client.async_trigger_retrieve_key.assert_awaited_once_with("home1", "gateway1")
    assert flow._pair_gateway_host == "192.0.2.1"
    assert result == {"step_id": "pair_button"}


async def test_initial_flow_stores_automatically_retrieved_keys():
    flow = config_flow.VeluxActiveConfigFlow()
    flow._username = "user"
    flow._entry_data = {"username": "user", "password": "pass"}
    flow._pair_gateway_host = "192.0.2.1"
    flow._pair_gateway_id = "gateway1"
    flow._async_retrieve_pairing_key = AsyncMock(
        return_value=SigningKey(sign_key_id="key-id", hash_sign_key="hash-key")
    )

    result = await flow.async_step_pair_button({})

    assert result == {
        "type": "create_entry",
        "title": "user",
        "data": {
            "username": "user",
            "password": "pass",
            "hash_sign_key": "hash-key",
            "sign_key_id": "key-id",
            "sign_key_gateway_id": "gateway1",
        },
    }


async def test_options_flow_returns_automatically_retrieved_keys():
    flow = config_flow.VeluxActiveOptionsFlow()
    flow._pair_gateway_host = "192.0.2.1"
    flow._pair_gateway_id = "gateway1"
    flow._async_retrieve_pairing_key = AsyncMock(
        return_value=SigningKey(sign_key_id="key-id", hash_sign_key="hash-key")
    )

    result = await flow.async_step_pair_button({})

    assert result == {
        "type": "create_entry",
        "title": "",
        "data": {
            "hash_sign_key": "hash-key",
            "sign_key_id": "key-id",
            "sign_key_gateway_id": "gateway1",
        },
    }
