"""Transport failures identify the VELUX endpoint without hiding API errors."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest
from pyatmo.auth import AbstractAsyncAuth
from pyatmo.const import (
    AUTH_REQ_ENDPOINT,
    GETHOMESDATA_ENDPOINT,
    GETHOMESTATUS_ENDPOINT,
)
from pyatmo.exceptions import ApiError
from velux_active.api import (
    VeluxActiveAuth,
    VeluxActiveCannotConnect,
    VeluxActiveClient,
    VeluxActiveInvalidAuth,
)
from velux_active.const import GETCONFIGS_ENDPOINT


@pytest.fixture
def auth():
    return VeluxActiveAuth(
        SimpleNamespace(), username="test@example.com", password="test-password"
    )


@pytest.mark.parametrize("endpoint", [GETHOMESDATA_ENDPOINT, GETHOMESTATUS_ENDPOINT])
async def test_polling_timeout_identifies_endpoint(auth, monkeypatch, endpoint):
    error = TimeoutError()
    request = AsyncMock(side_effect=error)
    monkeypatch.setattr(AbstractAsyncAuth, "async_post_api_request", request)

    with pytest.raises(VeluxActiveCannotConnect) as raised:
        await auth.async_post_api_request(endpoint)

    assert str(raised.value) == f"{endpoint}: TimeoutError"
    assert raised.value.__cause__ is error
    request.assert_awaited_once()


async def test_connection_failure_preserves_details(auth, monkeypatch):
    error = aiohttp.ClientConnectionError("connection lost")
    monkeypatch.setattr(
        AbstractAsyncAuth, "async_post_api_request", AsyncMock(side_effect=error)
    )

    with pytest.raises(VeluxActiveCannotConnect) as raised:
        await auth.async_post_api_request(GETHOMESTATUS_ENDPOINT)

    assert str(raised.value) == f"{GETHOMESTATUS_ENDPOINT}: connection lost"
    assert raised.value.__cause__ is error


async def test_parent_request_arguments_and_response_are_preserved(auth, monkeypatch):
    calls = []
    response = object()

    async def parent_request(self, endpoint, base_url=None, params=None):
        calls.append((self, endpoint, base_url, params))
        return response

    monkeypatch.setattr(AbstractAsyncAuth, "async_post_api_request", parent_request)
    params = {"home_id": "home1"}

    result = await auth.async_post_api_request(
        GETHOMESTATUS_ENDPOINT,
        base_url="https://example.test",
        params=params,
    )

    assert result is response
    assert calls == [(auth, GETHOMESTATUS_ENDPOINT, "https://example.test", params)]
    assert calls[0][3] is params


@pytest.mark.parametrize(
    "error",
    [
        ApiError("403 - Invalid access token"),
        VeluxActiveInvalidAuth("invalid_grant"),
        VeluxActiveCannotConnect(f"{AUTH_REQ_ENDPOINT}: TimeoutError"),
    ],
)
async def test_classified_api_and_auth_errors_propagate_unchanged(
    auth, monkeypatch, error
):
    monkeypatch.setattr(
        AbstractAsyncAuth, "async_post_api_request", AsyncMock(side_effect=error)
    )

    with pytest.raises(type(error)) as raised:
        await auth.async_post_api_request(GETHOMESTATUS_ENDPOINT)

    assert raised.value is error


async def test_oauth_timeout_identifies_token_endpoint(auth):
    error = TimeoutError()
    auth.websession = SimpleNamespace(post=Mock(side_effect=error))

    with pytest.raises(VeluxActiveCannotConnect) as raised:
        await auth.async_login()

    assert str(raised.value) == f"{AUTH_REQ_ENDPOINT}: TimeoutError"
    assert raised.value.__cause__ is error
    auth.websession.post.assert_called_once()


async def test_sync_api_connection_failure_identifies_endpoint():
    error = aiohttp.ClientConnectionError("connection lost")
    session = SimpleNamespace(request=Mock(side_effect=error))
    client = VeluxActiveClient(session, "test@example.com", "test-password")
    client._auth.async_get_access_token = AsyncMock(return_value="token")

    with pytest.raises(VeluxActiveCannotConnect) as raised:
        await client.async_get_configs("home1")

    assert str(raised.value) == f"{GETCONFIGS_ENDPOINT}: connection lost"
    assert raised.value.__cause__ is error
    session.request.assert_called_once()
