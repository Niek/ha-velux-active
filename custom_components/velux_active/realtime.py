"""VELUX app WebSocket event stream."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any

import aiohttp

WEBSOCKET_URL = "wss://app.velux-active.com/ws/"
WEBSOCKET_HEARTBEAT = 30.0
RECONNECT_DELAY = 5.0

_LOGGER = logging.getLogger(__name__)


def build_subscribe_message(
    access_token: str,
    app_version: str,
) -> dict[str, str]:
    """Build the subscription message used by the VELUX Android app."""
    return {
        "action": "Subscribe",
        "filter": "silent",
        "access_token": access_token,
        "app_type": "app_velux",
        "platform": "Android",
        "version": app_version,
    }


def extract_embedded_event(message: Mapping[str, Any]) -> dict[str, Any] | None:
    """Extract an embedded JSON event from one WebSocket message."""
    if message.get("push_type") != "embedded_json":
        return None

    extra_params = message.get("extra_params")
    if isinstance(extra_params, str):
        try:
            extra_params = json.loads(extra_params)
        except json.JSONDecodeError:
            return None

    return dict(extra_params) if isinstance(extra_params, Mapping) else None


async def async_iter_events(
    websession: aiohttp.ClientSession,
    access_token_getter: Callable[[], Awaitable[str]],
    app_version: str,
) -> AsyncIterator[dict[str, Any]]:
    """Yield VELUX embedded events, reconnecting after a connection ends."""
    while True:
        access_token = await access_token_getter()
        try:
            async with websession.ws_connect(
                WEBSOCKET_URL,
                heartbeat=WEBSOCKET_HEARTBEAT,
            ) as websocket:
                await websocket.send_json(
                    build_subscribe_message(access_token, app_version)
                )

                async for message in websocket:
                    if message.type is aiohttp.WSMsgType.ERROR:
                        raise aiohttp.ClientConnectionError(
                            str(websocket.exception() or "WebSocket connection failed")
                        )
                    if message.type is not aiohttp.WSMsgType.TEXT:
                        continue

                    try:
                        raw: Any = json.loads(message.data)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if not isinstance(raw, Mapping):
                        continue

                    status = raw.get("status")
                    if status is not None and status != "ok":
                        raise RuntimeError(f"WebSocket subscription failed: {status}")

                    if event := extract_embedded_event(raw):
                        yield event
        except (aiohttp.ClientError, OSError, TimeoutError) as err:
            _LOGGER.debug(
                "VELUX WebSocket disconnected; reconnecting: %s",
                err or type(err).__name__,
            )

        await asyncio.sleep(RECONNECT_DELAY)
