"""VELUX gateway connectivity parsing."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

DEVICE_UNREACHABLE_ERROR_CODE = 6


def gateway_reachable(raw_status: Mapping[str, Any], gateway_id: str) -> bool | None:
    """Return gateway reachability from a raw homestatus response."""
    body = raw_status.get("body")
    if not isinstance(body, Mapping):
        return None

    errors = body.get("errors")
    if isinstance(errors, list) and any(
        isinstance(error, Mapping)
        and error.get("code") == DEVICE_UNREACHABLE_ERROR_CODE
        and error.get("id") == gateway_id
        for error in errors
    ):
        return False

    home = body.get("home")
    modules = home.get("modules") if isinstance(home, Mapping) else None
    if isinstance(modules, list) and any(
        isinstance(module, Mapping) and module.get("id") == gateway_id
        for module in modules
    ):
        return True

    return None
