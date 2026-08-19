"""Tests for the VELUX app WebSocket protocol."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from velux_active.realtime import (
    WEBSOCKET_HEARTBEAT,
    WEBSOCKET_URL,
    async_iter_events,
    build_subscribe_message,
    extract_embedded_event,
)


class FakeWebSocket:
    """Minimal WebSocket async iterator."""

    def __init__(self, messages):
        self.messages = iter(messages)
        self.sent = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.messages)
        except StopIteration as err:
            raise StopAsyncIteration from err

    async def send_json(self, payload):
        self.sent.append(payload)

    def exception(self):
        return None


class FakeSession:
    """Return fake WebSocket connections in order."""

    def __init__(self, *websockets):
        self.websockets = iter(websockets)
        self.connections = []

    def ws_connect(self, url, **kwargs):
        self.connections.append((url, kwargs))
        return next(self.websockets)


def test_build_subscribe_message_matches_android_app():
    assert build_subscribe_message("token", "791302006") == {
        "action": "Subscribe",
        "filter": "silent",
        "access_token": "token",
        "app_type": "app_velux",
        "platform": "Android",
        "version": "791302006",
    }


def test_extract_embedded_event_accepts_object_and_json_string():
    event = {"timestamp": 123, "home": {"id": "home1"}}

    assert (
        extract_embedded_event({"push_type": "embedded_json", "extra_params": event})
        == event
    )
    assert (
        extract_embedded_event(
            {"push_type": "embedded_json", "extra_params": json.dumps(event)}
        )
        == event
    )
    assert extract_embedded_event({"status": "ok"}) is None
    assert (
        extract_embedded_event(
            {"push_type": "embedded_json", "extra_params": "not-json"}
        )
        is None
    )


async def test_iter_events_subscribes_and_yields_embedded_payload():
    event = {"timestamp": 123, "home": {"id": "home1"}}
    websocket = FakeWebSocket(
        [
            SimpleNamespace(
                type=aiohttp.WSMsgType.TEXT,
                data=json.dumps({"status": "ok"}),
            ),
            SimpleNamespace(
                type=aiohttp.WSMsgType.TEXT,
                data=json.dumps({"push_type": "embedded_json", "extra_params": event}),
            ),
        ]
    )
    session = FakeSession(websocket)
    get_access_token = AsyncMock(return_value="token")
    events = async_iter_events(session, get_access_token, "791302006")

    assert await anext(events) == event
    await events.aclose()

    assert session.connections == [(WEBSOCKET_URL, {"heartbeat": WEBSOCKET_HEARTBEAT})]
    assert websocket.sent == [build_subscribe_message("token", "791302006")]
    assert websocket.closed is True


async def test_iter_events_reconnects_with_fresh_access_token(monkeypatch):
    first_websocket = FakeWebSocket([])
    event = {"timestamp": 123, "home": {"id": "home1"}}
    second_websocket = FakeWebSocket(
        [
            SimpleNamespace(
                type=aiohttp.WSMsgType.TEXT,
                data=json.dumps({"push_type": "embedded_json", "extra_params": event}),
            )
        ]
    )
    session = FakeSession(first_websocket, second_websocket)
    get_access_token = AsyncMock(side_effect=["token1", "token2"])
    monkeypatch.setattr("velux_active.realtime.RECONNECT_DELAY", 0)
    events = async_iter_events(session, get_access_token, "791302006")

    assert await anext(events) == event
    await events.aclose()

    assert get_access_token.await_count == 2
    assert first_websocket.sent == [build_subscribe_message("token1", "791302006")]
    assert second_websocket.sent == [build_subscribe_message("token2", "791302006")]
    assert first_websocket.closed is True
    assert second_websocket.closed is True


async def test_iter_events_propagates_access_token_failure():
    session = FakeSession()
    get_access_token = AsyncMock(side_effect=RuntimeError("Authentication failed"))
    events = async_iter_events(session, get_access_token, "791302006")

    with pytest.raises(RuntimeError, match="Authentication failed"):
        await anext(events)

    assert session.connections == []


async def test_iter_events_propagates_subscription_failure():
    websocket = FakeWebSocket(
        [
            SimpleNamespace(
                type=aiohttp.WSMsgType.TEXT,
                data=json.dumps({"status": "error"}),
            )
        ]
    )
    session = FakeSession(websocket)
    events = async_iter_events(session, AsyncMock(return_value="token"), "791302006")

    with pytest.raises(RuntimeError, match="WebSocket subscription failed: error"):
        await anext(events)

    assert websocket.closed is True
