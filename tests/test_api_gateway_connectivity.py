"""Tests for VELUX gateway connectivity parsing."""

import pytest
from pyatmo.exceptions import ApiHomeReachabilityError
from velux_active.api import (
    VeluxActiveClient,
    _extract_gateway_connectivity,
    _extract_gateway_status,
)


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


def test_extract_gateway_status_keeps_raw_wifi_diagnostics():
    status = {
        "body": {
            "home": {
                "modules": [
                    {
                        "id": "gateway1",
                        "type": "NXG",
                        "wifi_strength": 47,
                        "wifi_state": "full",
                    },
                    {
                        "id": "sensor1",
                        "type": "NXS",
                        "rf_strength": 59,
                    },
                ]
            }
        }
    }

    gateway_status = _extract_gateway_status(
        {"home1": FakeHome("gateway1")}, {"home1": status}
    )

    assert gateway_status == {
        "gateway1": {
            "id": "gateway1",
            "type": "NXG",
            "wifi_strength": 47,
            "wifi_state": "full",
            "home_id": "home1",
        }
    }


async def test_async_update_keeps_gateway_unreachable_status():
    status = {
        "body": {
            "home": {"id": "home1"},
            "errors": [{"code": 6, "id": "gateway1"}],
        }
    }
    client = _client_returning(status)

    data = await client.async_update()

    assert data.gateway_connectivity == {"gateway1": False}


async def test_async_update_propagates_other_reachability_errors():
    status = {
        "body": {
            "home": {"id": "home1"},
            "errors": [{"code": 99, "id": "gateway1"}],
        }
    }
    client = _client_returning(status)

    with pytest.raises(ApiHomeReachabilityError):
        await client.async_update()
