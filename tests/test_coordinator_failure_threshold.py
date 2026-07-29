"""Standalone checks for coordinator failure-threshold behavior.

Run: python3.13 tests/test_coordinator_failure_threshold.py
"""

import logging
import sys
import types
from pathlib import Path


class _GenericStub:
    @classmethod
    def __class_getitem__(cls, item):
        return cls


class _UpdateFailed(Exception):
    pass


class _ApiError(Exception):
    pass


def _install_module(name, **attributes):
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


for package_name in ("homeassistant", "homeassistant.helpers", "pyatmo"):
    package = _install_module(package_name)
    package.__path__ = []

_install_module("homeassistant.config_entries", ConfigEntry=_GenericStub)
_install_module("homeassistant.core", HomeAssistant=object)
_install_module(
    "homeassistant.exceptions",
    ConfigEntryAuthFailed=type("ConfigEntryAuthFailed", (Exception,), {}),
)
_install_module(
    "homeassistant.helpers.update_coordinator",
    DataUpdateCoordinator=_GenericStub,
    UpdateFailed=_UpdateFailed,
)
_install_module(
    "pyatmo.exceptions",
    ApiError=_ApiError,
    ApiHomeReachabilityError=type("ApiHomeReachabilityError", (_ApiError,), {}),
)

_PKG = Path(__file__).resolve().parents[1] / "custom_components" / "velux_active"
_pkg = types.ModuleType("velux_active")
_pkg.__path__ = [str(_PKG)]
sys.modules["velux_active"] = _pkg
_install_module(
    "velux_active.api",
    VeluxActiveCannotConnect=type("VeluxActiveCannotConnect", (Exception,), {}),
    VeluxActiveClient=object,
    VeluxActiveData=object,
    VeluxActiveInvalidAuth=type("VeluxActiveInvalidAuth", (Exception,), {}),
)
_install_module(
    "velux_active.const",
    DOMAIN="velux_active",
    LOGGER=logging.getLogger("velux_active_test"),
    UPDATE_INTERVAL=object(),
)

from velux_active.coordinator import (
    _FAILURE_THRESHOLD,
    VeluxActiveDataUpdateCoordinator,
)


def test_failure_threshold_marks_update_failed():
    coordinator = object.__new__(VeluxActiveDataUpdateCoordinator)
    coordinator._consecutive_failures = 0
    coordinator._fast_poll_task = None
    coordinator.data = previous_data = object()
    error = _ApiError("offline")

    for _ in range(_FAILURE_THRESHOLD - 1):
        assert coordinator._handle_update_error(error) is previous_data

    try:
        coordinator._handle_update_error(error)
    except _UpdateFailed:
        pass
    else:
        raise AssertionError("Failure threshold did not raise UpdateFailed")


if __name__ == "__main__":
    test_failure_threshold_marks_update_failed()
    print("all passed")
