"""Shared setstate requests report expected failures as Home Assistant errors."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest
from homeassistant.exceptions import HomeAssistantError
from velux_active.api import (
    DEFAULT_TIMEOUT,
    VeluxActiveCannotConnect,
    VeluxActiveInvalidAuth,
)
from velux_active.const import VELUX_API_URL, VELUX_APP_TYPE, VELUX_APP_VERSION
from velux_active.entity import async_post_setstate

ACTION = "Silent operation command"
MODULES = [{"id": "shutter1", "bridge": "gateway1", "silent": True}]


@pytest.fixture
def request_context():
    response = SimpleNamespace(
        status=200,
        ok=True,
        text=AsyncMock(return_value='{"status":"ok","body":{}}'),
    )
    pending = AsyncMock()
    pending.__aenter__.return_value = response
    session = SimpleNamespace(post=Mock(return_value=pending))
    return SimpleNamespace(
        response=response,
        pending=pending,
        session=session,
        hass=SimpleNamespace(session=session),
        client=SimpleNamespace(
            _auth=SimpleNamespace(
                async_get_access_token=AsyncMock(return_value="token")
            )
        ),
    )


async def send(context):
    await async_post_setstate(
        context.hass,
        context.client,
        "home1",
        "Europe/Amsterdam",
        MODULES,
        action=ACTION,
    )


async def test_success_preserves_request_and_sets_explicit_timeout(request_context):
    await send(request_context)

    request_context.session.post.assert_called_once()
    args, kwargs = request_context.session.post.call_args
    assert args == (f"{VELUX_API_URL}/syncapi/v1/setstate",)
    assert kwargs["json"] == {
        "app_type": VELUX_APP_TYPE,
        "app_version": VELUX_APP_VERSION,
        "home": {
            "id": "home1",
            "timezone": "Europe/Amsterdam",
            "modules": MODULES,
        },
    }
    assert kwargs["headers"] == {
        "Authorization": "Bearer token",
        "Content-Type": "application/json",
    }
    assert isinstance(kwargs["timeout"], aiohttp.ClientTimeout)
    assert kwargs["timeout"].total == DEFAULT_TIMEOUT


async def test_connection_timeout_is_contextual_and_not_retried(request_context):
    error = aiohttp.ConnectionTimeoutError("Connection timeout to host")
    request_context.pending.__aenter__.side_effect = error

    with pytest.raises(HomeAssistantError, match=ACTION) as raised:
        await send(request_context)

    assert "timed out" in str(raised.value)
    assert raised.value.__cause__ is error
    request_context.session.post.assert_called_once()


@pytest.mark.parametrize(
    ("stage", "error"),
    [
        ("token", VeluxActiveCannotConnect()),
        ("connect", aiohttp.ClientError()),
        ("read", aiohttp.ClientPayloadError("Response payload is incomplete")),
    ],
)
async def test_expected_connection_failures_are_wrapped(request_context, stage, error):
    operation = {
        "token": request_context.client._auth.async_get_access_token,
        "connect": request_context.pending.__aenter__,
        "read": request_context.response.text,
    }[stage]
    operation.side_effect = error

    with pytest.raises(HomeAssistantError, match=ACTION) as raised:
        await send(request_context)

    assert "failed" in str(raised.value)
    assert (str(error) or type(error).__name__) in str(raised.value)
    assert raised.value.__cause__ is error
    assert request_context.session.post.call_count <= 1


async def test_auth_failure_has_action_context(request_context):
    error = VeluxActiveInvalidAuth("invalid_grant")
    request_context.client._auth.async_get_access_token.side_effect = error

    with pytest.raises(HomeAssistantError, match=ACTION) as raised:
        await send(request_context)

    assert "authentication failed" in str(raised.value)
    assert raised.value.__cause__ is error
    request_context.session.post.assert_not_called()


async def test_invalid_response_encoding_is_a_handled_failure(request_context):
    error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    request_context.response.text.side_effect = error

    with pytest.raises(HomeAssistantError, match=ACTION) as raised:
        await send(request_context)

    assert "invalid response encoding" in str(raised.value)
    assert raised.value.__cause__ is error
    request_context.session.post.assert_called_once()


@pytest.mark.parametrize("text", ["", "<html>Service unavailable</html>"])
async def test_http_error_keeps_status_without_json(request_context, text):
    request_context.response.status = 503
    request_context.response.ok = False
    request_context.response.text.return_value = text

    with pytest.raises(HomeAssistantError, match=ACTION) as raised:
        await send(request_context)

    assert "503" in str(raised.value)
    request_context.session.post.assert_called_once()


@pytest.mark.parametrize(
    "text",
    ["", "<html>Unexpected response</html>", "[]", '{"body":[]}', '{"body":null}'],
)
async def test_invalid_success_response_is_a_handled_failure(request_context, text):
    request_context.response.text.return_value = text

    with pytest.raises(HomeAssistantError, match=ACTION):
        await send(request_context)


async def test_body_errors_remain_command_failures(request_context):
    request_context.response.text.return_value = (
        '{"status":"ok","body":{"errors":[{"code":9,"id":"shutter1"}]}}'
    )

    with pytest.raises(HomeAssistantError, match=ACTION) as raised:
        await send(request_context)

    assert "9" in str(raised.value)
    assert "shutter1" in str(raised.value)


@pytest.mark.parametrize("error", [RuntimeError("bug"), asyncio.CancelledError()])
async def test_programming_errors_and_cancellation_propagate(request_context, error):
    request_context.pending.__aenter__.side_effect = error

    with pytest.raises(type(error)) as raised:
        await send(request_context)

    assert raised.value is error
    request_context.session.post.assert_called_once()
