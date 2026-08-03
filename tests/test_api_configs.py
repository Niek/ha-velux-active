"""Tests for VELUX controlled opener configuration requests."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from velux_active.api import (
    VeluxActiveCannotConnect,
    VeluxActiveClient,
    _extract_controlled_openers,
)


class FakeResponse:
    """Minimal aiohttp response context manager."""

    def __init__(self, payload, *, status=200):
        self.payload = payload
        self.status = status
        self.ok = status < 400

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def text(self):
        return json.dumps(self.payload)


class FakeSession:
    """Capture sync API requests."""

    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return FakeResponse(self.payload)


def _client_with_response(payload):
    client = object.__new__(VeluxActiveClient)
    session = FakeSession(payload)
    client._auth = SimpleNamespace(
        websession=session,
        async_get_access_token=AsyncMock(return_value="access-token"),
    )
    client._controlled_openers_by_home = {}
    return client, session


class FakeModule:
    """Minimal pyatmo module used by async_update."""

    name = "Indoor sensor"
    room_id = None


class FakeHome:
    """Minimal pyatmo home used by async_update."""

    entity_id = "home1"
    name = "Test home"

    def __init__(self):
        self.modules = {"sensor1": FakeModule()}
        self.rooms = {}

    async def update(self, raw_data, do_raise_for_reachability_error=False):
        return None


class FakeAccount:
    """Minimal pyatmo account used by async_update."""

    user = "test@example.com"

    def __init__(self):
        self.homes = {"home1": FakeHome()}


def _client_for_update(configs):
    client = object.__new__(VeluxActiveClient)
    client._account = FakeAccount()
    client._controlled_openers_by_home = {}
    client.async_get_raw_homestatus = AsyncMock(
        return_value={
            "status": "ok",
            "body": {
                "home": {
                    "id": "home1",
                    "modules": [{"id": "sensor1", "type": "NXS"}],
                }
            },
        }
    )
    client.async_get_configs = AsyncMock(return_value=configs)
    return client


def test_extract_controlled_openers_uses_module_level_config():
    raw = {
        "status": "ok",
        "body": {
            "home": {
                "modules": [
                    {"id": "gateway1", "algo_enabled": True},
                    {
                        "id": "sensor1",
                        "bridge": "gateway1",
                        "controlled_openers": "windows",
                    },
                ],
                "rooms": [{"id": "room1", "algo_enable_temperature": True}],
            }
        },
    }

    assert _extract_controlled_openers("home1", raw) == {
        "sensor1": {
            "home_id": "home1",
            "bridge": "gateway1",
            "controlled_openers": "windows",
        }
    }


async def test_async_update_fetches_configs_for_nxs_home():
    configs = {
        "status": "ok",
        "body": {
            "home": {
                "modules": [
                    {
                        "id": "sensor1",
                        "bridge": "gateway1",
                        "controlled_openers": "windows",
                    }
                ]
            }
        },
    }
    client = _client_for_update(configs)

    data = await client.async_update()

    client.async_get_configs.assert_awaited_once_with("home1")
    assert data.controlled_openers == {
        "sensor1": {
            "home_id": "home1",
            "bridge": "gateway1",
            "controlled_openers": "windows",
        }
    }


async def test_async_update_keeps_cached_config_when_getconfigs_fails():
    client = _client_for_update({})
    client._controlled_openers_by_home = {
        "home1": {
            "sensor1": {
                "home_id": "home1",
                "bridge": "gateway1",
                "controlled_openers": "windows",
            }
        }
    }
    client.async_get_configs.side_effect = VeluxActiveCannotConnect("temporary")

    data = await client.async_update()

    assert data.controlled_openers["sensor1"]["controlled_openers"] == "windows"


async def test_get_configs_uses_home_query_and_bearer_token():
    client, session = _client_with_response({"status": "ok", "body": {}})

    await client.async_get_configs("home1")

    method, url, request = session.requests[0]
    assert method == "GET"
    assert url == "https://app.velux-active.com/syncapi/v1/getconfigs"
    assert request["params"] == {"home_id": "home1"}
    assert request["headers"] == {"Authorization": "Bearer access-token"}
    assert request["timeout"].total == 10.0


async def test_set_controlled_openers_sends_confirmed_payload_and_updates_cache():
    client, session = _client_with_response({"status": "ok", "time_server": 1})

    await client.async_set_controlled_openers(
        "home1", "sensor1", "gateway1", "external_covers"
    )

    method, url, request = session.requests[0]
    assert method == "POST"
    assert url == "https://app.velux-active.com/syncapi/v1/setconfigs"
    assert request["json"] == {
        "home_id": "home1",
        "home": {
            "modules": [
                {
                    "id": "sensor1",
                    "bridge": "gateway1",
                    "controlled_openers": "external_covers",
                }
            ]
        },
    }
    assert request["headers"] == {
        "Authorization": "Bearer access-token",
        "Content-Type": "application/json",
    }
    assert client._controlled_openers_by_home["home1"]["sensor1"] == {
        "home_id": "home1",
        "bridge": "gateway1",
        "controlled_openers": "external_covers",
    }


async def test_set_controlled_openers_rejects_product_level_error():
    client, _ = _client_with_response(
        {"status": "error", "body": {"errors": [{"code": 9}]}}
    )

    with pytest.raises(VeluxActiveCannotConnect):
        await client.async_set_controlled_openers(
            "home1", "sensor1", "gateway1", "windows"
        )
