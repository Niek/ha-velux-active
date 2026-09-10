"""Tests for coordinator behavior."""

import asyncio
import logging
from json import JSONDecodeError
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import velux_active.coordinator as coordinator_module
from aiohttp import ClientConnectionError
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pyatmo.exceptions import ApiError
from velux_active.api import VeluxActiveCannotConnect, VeluxActiveInvalidAuth
from velux_active.coordinator import (
    _FAILURE_THRESHOLD,
    VeluxActiveDataUpdateCoordinator,
)


@pytest.fixture
def coordinator():
    coordinator = object.__new__(VeluxActiveDataUpdateCoordinator)
    coordinator._consecutive_failures = 0
    coordinator._fast_poll_task = None
    coordinator._topology_loaded = True
    coordinator.data = object()
    coordinator.client = SimpleNamespace(
        async_setup=AsyncMock(),
        async_update=AsyncMock(),
        async_reauthenticate=AsyncMock(),
    )
    return coordinator


@pytest.mark.parametrize(
    "error",
    [
        ApiError("offline"),
        VeluxActiveCannotConnect("offline"),
        ClientConnectionError("offline"),
        JSONDecodeError("Expecting value", "", 0),
        TimeoutError(),
    ],
)
async def test_update_failure_threshold_and_recovery(coordinator, error, caplog):
    previous_data = coordinator.data
    recovered_data = object()
    coordinator.client.async_update.side_effect = [error] * (_FAILURE_THRESHOLD + 1) + [
        recovered_data,
        error,
    ]

    for _ in range(_FAILURE_THRESHOLD - 1):
        assert await coordinator._async_update_data() is previous_data

    for _ in range(2):
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    assert coordinator._consecutive_failures == _FAILURE_THRESHOLD + 1
    assert not caplog.records  # HA owns outage/recovery transition logging.
    coordinator.client.async_reauthenticate.assert_not_awaited()

    assert await coordinator._async_update_data() is recovered_data
    assert coordinator._consecutive_failures == 0
    coordinator.data = recovered_data
    assert await coordinator._async_update_data() is recovered_data
    assert coordinator._consecutive_failures == 1


async def test_timeout_error_has_readable_message(coordinator, caplog):
    coordinator.client.async_update.side_effect = TimeoutError()

    with caplog.at_level(logging.DEBUG, logger="velux_active"):
        for _ in range(_FAILURE_THRESHOLD - 1):
            await coordinator._async_update_data()

    assert "keeping previous data: TimeoutError" in caplog.text
    with pytest.raises(UpdateFailed, match="VELUX ACTIVE: TimeoutError"):
        await coordinator._async_update_data()


async def test_first_update_failure_does_not_return_missing_data(coordinator):
    coordinator.data = None
    coordinator.client.async_update.side_effect = VeluxActiveCannotConnect("offline")

    with pytest.raises(UpdateFailed, match="offline"):
        await coordinator._async_update_data()


@pytest.mark.parametrize("failure_stage", ["reauthenticate", "retry"])
@pytest.mark.parametrize("invalid_auth", [False, True])
async def test_token_recovery_errors_use_shared_error_handler(
    coordinator, failure_stage, invalid_auth
):
    error = (
        VeluxActiveInvalidAuth("invalid_grant")
        if invalid_auth
        else VeluxActiveCannotConnect("offline")
    )
    coordinator.client.async_update.side_effect = [
        ApiError("403 invalid access token"),
        error,
    ]
    if failure_stage == "reauthenticate":
        coordinator.client.async_reauthenticate.side_effect = error

    if invalid_auth:
        with pytest.raises(ConfigEntryAuthFailed) as caught:
            await coordinator._async_update_data()
        assert caught.value.__cause__ is error
    else:
        assert await coordinator._async_update_data() is coordinator.data
        assert coordinator._consecutive_failures == 1

    coordinator.client.async_reauthenticate.assert_awaited_once()
    assert coordinator.client.async_update.await_count == (
        1 if failure_stage == "reauthenticate" else 2
    )


async def test_token_recovery_success_resets_failure_count(coordinator):
    recovered_data = object()
    coordinator._consecutive_failures = _FAILURE_THRESHOLD
    coordinator.client.async_update.side_effect = [
        ApiError("403 invalid access token"),
        recovered_data,
    ]

    assert await coordinator._async_update_data() is recovered_data

    assert coordinator._consecutive_failures == 0
    coordinator.client.async_reauthenticate.assert_awaited_once()
    coordinator.client.async_setup.assert_awaited_once()
    assert coordinator._topology_loaded is True


async def test_realtime_listener_notifies_only_for_changed_events():
    processed = asyncio.Event()

    class FakeClient:
        async def async_realtime_events(self):
            yield {"changed": True}
            yield {"changed": False}
            await asyncio.Event().wait()

        def apply_realtime_cover_event(self, event):
            if not event["changed"]:
                processed.set()
            return event["changed"]

    coordinator = object.__new__(VeluxActiveDataUpdateCoordinator)
    coordinator.client = FakeClient()
    updates = []
    coordinator.last_update_success = False
    coordinator.async_update_listeners = lambda: updates.append(True)
    coordinator.async_set_updated_data = lambda data: pytest.fail(
        "Realtime updates must not reset coordinator polling state"
    )

    task = asyncio.create_task(coordinator._async_listen_realtime())
    await processed.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert updates == [True]
    assert coordinator.last_update_success is False


async def test_realtime_listener_restarts_after_error(monkeypatch):
    restarted = asyncio.Event()

    class FakeClient:
        calls = 0

        async def async_realtime_events(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("subscription failed")
            restarted.set()
            await asyncio.Event().wait()
            yield

        def apply_realtime_cover_event(self, event):
            return False

    coordinator = object.__new__(VeluxActiveDataUpdateCoordinator)
    coordinator.client = client = FakeClient()
    monkeypatch.setattr(coordinator_module, "RECONNECT_DELAY", 0)

    task = asyncio.create_task(coordinator._async_listen_realtime())
    await restarted.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert client.calls == 2


async def test_start_realtime_uses_config_entry_background_task():
    class FakeConfigEntry:
        def __init__(self):
            self.calls = []

        def async_create_background_task(self, hass, target, name):
            self.calls.append((hass, name))
            return asyncio.create_task(target)

    coordinator = object.__new__(VeluxActiveDataUpdateCoordinator)
    coordinator.hass = hass = object()
    coordinator.config_entry = config_entry = FakeConfigEntry()
    coordinator._realtime_task = None

    async def listen_once():
        return None

    coordinator._async_listen_realtime = listen_once

    coordinator.start_realtime()
    await coordinator._realtime_task

    assert config_entry.calls == [(hass, "velux_active websocket")]


async def test_stop_realtime_cancels_listener_task():
    coordinator = object.__new__(VeluxActiveDataUpdateCoordinator)
    started = asyncio.Event()

    async def wait_forever():
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(wait_forever())
    await started.wait()
    coordinator._realtime_task = task

    await coordinator.async_stop_realtime()

    assert coordinator._realtime_task is None
    assert task.cancelled()
