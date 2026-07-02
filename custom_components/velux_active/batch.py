"""Batched, signed setstate sender for roof-window commands.

Kept free of Home Assistant imports (the session is injected) so the batching
and nonce behaviour can be unit-tested standalone (see tests/test_batch.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from .const import VELUX_API_URL, VELUX_APP_TYPE, VELUX_APP_VERSION
from .signing import allocate_nonces, build_signed_modules

_LOGGER = logging.getLogger(__name__)


class BatchCommandError(Exception):
    """A batched signed command failed. Callers wrap this for the UI."""


class BatchCommandManager:
    """Collect signed window commands and send them in a single setstate call.

    When HA calls open_cover on a group, it calls each entity's open_cover
    sequentially. We collect all commands that arrive within a short window
    (150 ms) and send them in one request with incrementing nonces, matching
    the official Velux app. The API rejects a reused (timestamp, nonce) pair,
    so nonce state is carried across batches.
    """

    def __init__(self) -> None:
        self._pending: list[dict] = []
        self._task: asyncio.Task | None = None
        self._home_id: str | None = None
        self._bridge_id: str | None = None
        self._session = None
        self._access_token_getter = None
        self._hash_sign_key: str = ""
        self._sign_key_id: str = ""
        self._timezone: str = "UTC"
        self._last_ts: int = 0
        self._last_nonce: int = -1

    def setup(
        self,
        home_id: str,
        bridge_id: str,
        session,
        access_token_getter,
        hash_sign_key: str,
        sign_key_id: str,
        timezone: str,
    ) -> None:
        self._home_id = home_id
        self._bridge_id = bridge_id
        self._session = session
        self._access_token_getter = access_token_getter
        self._hash_sign_key = hash_sign_key
        self._sign_key_id = sign_key_id
        self._timezone = timezone

    def queue(self, module_id: str, raw_position: int) -> asyncio.Future:
        """Queue a window command. Returns a Future resolved when the batch fires."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending.append({"id": module_id, "position": raw_position, "future": future})

        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._fire_after_delay())

        return future

    async def _fire_after_delay(self) -> None:
        """Wait briefly for more commands, then send. Loop so commands queued
        while a send is in flight are not stranded with an unresolved future."""
        while self._pending:
            await asyncio.sleep(0.15)
            await self._send_batch()

    async def _send_batch(self) -> None:
        if not self._pending:
            return

        # Deduplicate by module ID — keep only the latest command per window.
        seen: dict[str, dict] = {}
        for cmd in self._pending:
            seen[cmd["id"]] = cmd
        commands = list(seen.values())
        self._pending.clear()

        # Everything that can fail (e.g. invalid Base64 signing key, network)
        # must be inside the try so every queued future gets resolved.
        error: Exception | None = None
        try:
            timestamp, base_nonce = allocate_nonces(
                int(time.time()), self._last_ts, self._last_nonce
            )
            modules = build_signed_modules(
                commands,
                timestamp,
                base_nonce,
                self._bridge_id,
                self._sign_key_id,
                self._hash_sign_key,
            )
            self._last_ts = timestamp
            self._last_nonce = base_nonce + len(commands) - 1

            payload = {
                "app_type": VELUX_APP_TYPE,
                "app_version": VELUX_APP_VERSION,
                "home": {
                    "id": self._home_id,
                    "timezone": self._timezone,
                    "modules": modules,
                },
            }
            _LOGGER.debug(
                "Sending batched setstate for %d window(s) at timestamp %d",
                len(modules),
                timestamp,
            )

            access_token = await self._access_token_getter()
            async with self._session.post(
                f"{VELUX_API_URL}/syncapi/v1/setstate",
                json=payload,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            ) as response:
                text = await response.text()
                if not text.strip():
                    error = BatchCommandError(
                        f"Empty response from API (status {response.status}) "
                        "— possibly rate limited or token expired"
                    )
                elif not response.ok:
                    error = BatchCommandError(f"Signed setstate failed: {text}")
                else:
                    api_errors = json.loads(text).get("body", {}).get("errors", [])
                    if api_errors:
                        error = BatchCommandError(f"Signed setstate errors: {api_errors}")
        except Exception as err:
            error = err if isinstance(err, BatchCommandError) else BatchCommandError(str(err))

        for cmd in commands:
            future = cmd["future"]
            if not future.done():
                if error:
                    future.set_exception(error)
                else:
                    future.set_result(None)


# One batch manager per config entry (shared across all window entities).
_batch_managers: dict[str, BatchCommandManager] = {}


def get_batch_manager(entry_id: str) -> BatchCommandManager:
    if entry_id not in _batch_managers:
        _batch_managers[entry_id] = BatchCommandManager()
    return _batch_managers[entry_id]
