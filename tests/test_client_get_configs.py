"""Tests for the client.py get-configs command."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import client


class FakeResponse:
    """Minimal aiohttp response context manager."""

    status = 200
    ok = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def text(self):
        return '{"body":{"home":{"id":"home1","modules":[]}}}'


class FakeSession:
    """Capture the getconfigs request."""

    def __init__(self):
        self.request = None

    def get(self, url, **kwargs):
        self.request = (url, kwargs)
        return FakeResponse()


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
    url, request = session.request
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
