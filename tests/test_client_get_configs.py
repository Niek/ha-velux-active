"""Tests for the client.py sync configuration commands."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import client


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

    def __init__(self, payload=None):
        self.payload = payload or {
            "status": "ok",
            "body": {"home": {"id": "home1", "modules": []}},
        }
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append(("GET", url, kwargs))
        return FakeResponse(self.payload)

    def post(self, url, **kwargs):
        self.requests.append(("POST", url, kwargs))
        return FakeResponse(self.payload)


async def test_get_sync_configs_uses_bearer_token_and_home_id():
    session = FakeSession()
    auth = SimpleNamespace(
        websession=session,
        async_get_access_token=AsyncMock(return_value="access-token"),
    )
    args = SimpleNamespace(
        sync_base_url="https://app.velux-active.com/",
        timeout=10.0,
    )

    result = await client.get_sync_configs(auth, args, home_id="home1")

    assert result["body"]["home"]["id"] == "home1"
    method, url, request = session.requests[0]
    assert method == "GET"
    assert url == "https://app.velux-active.com/syncapi/v1/getconfigs"
    assert request["params"] == {"home_id": "home1"}
    assert request["headers"] == {"Authorization": "Bearer access-token"}
    assert request["timeout"].total == 10.0


def test_parser_accepts_get_configs_command():
    args = client.build_parser().parse_args(
        ["get-configs", "home1", "--access-token", "access-token"]
    )

    assert args.command == "get-configs"
    assert args.home == "home1"
    assert args.access_token == "access-token"


def test_parser_accepts_watch_events_command():
    args = client.build_parser().parse_args(
        ["watch-events", "--access-token", "access-token"]
    )

    assert args.command == "watch-events"
    assert args.access_token == "access-token"


async def test_set_sync_controlled_openers_uses_confirmed_payload():
    session = FakeSession()
    auth = SimpleNamespace(
        websession=session,
        async_get_access_token=AsyncMock(return_value="access-token"),
    )
    args = SimpleNamespace(
        sync_base_url="https://app.velux-active.com/",
        timeout=10.0,
    )

    await client.set_sync_controlled_openers(
        auth,
        args,
        home_id="home1",
        module_id="sensor1",
        bridge_id="gateway1",
        controlled_openers="external_covers",
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


async def test_set_sync_controlled_openers_rejects_product_error():
    session = FakeSession({"status": "error", "body": {"errors": [{"code": 9}]}})
    auth = SimpleNamespace(
        websession=session,
        async_get_access_token=AsyncMock(return_value="access-token"),
    )
    args = SimpleNamespace(
        sync_base_url="https://app.velux-active.com/",
        timeout=10.0,
    )

    with pytest.raises(RuntimeError, match="setconfigs failed"):
        await client.set_sync_controlled_openers(
            auth,
            args,
            home_id="home1",
            module_id="sensor1",
            bridge_id="gateway1",
            controlled_openers="windows",
        )


def test_resolve_controlled_openers_target_uses_nxs_module():
    homesdata = {
        "body": {
            "homes": [
                {
                    "id": "home1",
                    "name": "Home",
                    "modules": [
                        {
                            "id": "sensor1",
                            "name": "Bedroom sensor",
                            "type": "NXS",
                            "bridge": "gateway1",
                        },
                        {
                            "id": "switch1",
                            "name": "Departure switch",
                            "type": "NXD",
                            "bridge": "gateway1",
                        },
                    ],
                }
            ]
        }
    }

    assert client.resolve_controlled_openers_target(homesdata, "Bedroom sensor") == (
        "home1",
        "sensor1",
        "gateway1",
    )


def test_parser_accepts_set_controlled_openers_command():
    args = client.build_parser().parse_args(
        [
            "set-controlled-openers",
            "sensor1",
            "external_covers",
            "--access-token",
            "access-token",
        ]
    )

    assert args.command == "set-controlled-openers"
    assert args.module == "sensor1"
    assert args.controlled_openers == "external_covers"
