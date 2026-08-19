"""Tests for coordinator behavior."""

import asyncio

import pytest
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
    class FakeClient:
        async def async_realtime_events(self):
            yield {"changed": True}
            yield {"changed": False}

        def apply_realtime_cover_event(self, event):
            return event["changed"]

    coordinator = object.__new__(VeluxActiveDataUpdateCoordinator)
    coordinator.client = FakeClient()
    coordinator.data = data = object()
    updates = []
    coordinator.async_set_updated_data = updates.append

    await coordinator._async_listen_realtime()

    assert updates == [data]


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
