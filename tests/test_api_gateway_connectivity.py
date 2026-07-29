"""Standalone checks for VELUX gateway connectivity parsing.

Run: uv run --with aiohttp --with pyatmo==9.4.0 python tests/test_api_gateway_connectivity.py
"""

import asyncio
import sys
import types
from pathlib import Path

from pyatmo.exceptions import ApiHomeReachabilityError

# Bootstrap a stub package so api.py's relative imports resolve without HA.
_PKG = Path(__file__).resolve().parents[1] / "custom_components" / "velux_active"
_pkg = types.ModuleType("velux_active")
_pkg.__path__ = [str(_PKG)]
sys.modules.setdefault("velux_active", _pkg)

from velux_active.api import VeluxActiveClient, _extract_gateway_connectivity


class NXG:
    pass


class FakeHome:
    def __init__(self, *gateway_ids):
        self.modules = {gateway_id: NXG() for gateway_id in gateway_ids}


class UnreachableHome(FakeHome):
    def __init__(self, *gateway_ids):
        super().__init__(*gateway_ids)
        self.entity_id = "home1"
        self.name = "Test Home"
        self.rooms = {}

    async def update(self, raw_data, do_raise_for_reachability_error=False):
        raise ApiHomeReachabilityError("Gateway unreachable")


class FakeAccount:
    def __init__(self):
        self.user = "test@example.com"
        self.homes = {"home1": UnreachableHome("gateway1")}


def _client_returning(status):
    client = object.__new__(VeluxActiveClient)
    client._account = FakeAccount()

    async def async_get_raw_homestatus(home_id):
        assert home_id == "home1"
        return status

    client.async_get_raw_homestatus = async_get_raw_homestatus
    return client


def test_extract_gateway_connectivity_reports_gateway_present_as_connected():
    status = {
        "body": {
            "home": {
                "id": "home1",
                "modules": [{"id": "gateway1", "type": "NXG"}],
            }
        }
    }

    connectivity = _extract_gateway_connectivity(
        {"home1": FakeHome("gateway1")}, {"home1": status}
    )

    assert connectivity == {"gateway1": True}


def test_extract_gateway_connectivity_scans_all_errors_for_unreachable_gateway():
    status = {
        "body": {
            "home": {"id": "home1"},
            "errors": [
                {"code": 99, "id": "other"},
                {"code": 6, "id": "gateway1"},
            ],
        }
    }

    connectivity = _extract_gateway_connectivity(
        {"home1": FakeHome("gateway1")}, {"home1": status}
    )

    assert connectivity == {"gateway1": False}


def test_extract_gateway_connectivity_leaves_ambiguous_status_unknown():
    status = {
        "body": {
            "home": {"id": "home1"},
            "errors": [{"code": 6, "id": "other-gateway"}],
        }
    }

    connectivity = _extract_gateway_connectivity(
        {"home1": FakeHome("gateway1")}, {"home1": status}
    )

    assert connectivity == {"gateway1": None}


def test_extract_gateway_connectivity_leaves_missing_status_unknown():
    connectivity = _extract_gateway_connectivity({"home1": FakeHome("gateway1")}, {})

    assert connectivity == {"gateway1": None}


def test_async_update_keeps_gateway_unreachable_status():
    status = {
        "body": {
            "home": {"id": "home1"},
            "errors": [{"code": 6, "id": "gateway1"}],
        }
    }
    client = _client_returning(status)

    data = asyncio.run(client.async_update())

    assert data.gateway_connectivity == {"gateway1": False}


def test_async_update_propagates_other_reachability_errors():
    status = {
        "body": {
            "home": {"id": "home1"},
            "errors": [{"code": 99, "id": "gateway1"}],
        }
    }
    client = _client_returning(status)

    try:
        asyncio.run(client.async_update())
    except ApiHomeReachabilityError:
        pass
    else:
        raise AssertionError("Non-gateway reachability error was suppressed")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")
    print("all passed")
