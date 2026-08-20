"""Tests for coordinator behavior."""

import asyncio

import pytest
import velux_active.coordinator as coordinator_module
from homeassistant.helpers.update_coordinator import UpdateFailed
from pyatmo.exceptions import ApiError
from velux_active.coordinator import (
    _FAILURE_THRESHOLD,
    VeluxActiveDataUpdateCoordinator,
)


def test_failure_threshold_marks_update_failed():
    coordinator = object.__new__(VeluxActiveDataUpdateCoordinator)
    coordinator._consecutive_failures = 0
    coordinator._fast_poll_task = None
    coordinator.data = previous_data = object()
    error = ApiError("offline")

    for _ in range(_FAILURE_THRESHOLD - 1):
        assert coordinator._handle_update_error(error) is previous_data

    with pytest.raises(UpdateFailed):
        coordinator._handle_update_error(error)


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
