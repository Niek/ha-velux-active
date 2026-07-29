"""Tests for coordinator failure-threshold behavior."""

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
