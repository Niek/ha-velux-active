"""Standalone checks for client.py raw status serialization.

Run: uv run --with aiohttp --with cryptography --with pyatmo==9.4.0 python tests/test_client_status_serialization.py
"""

import asyncio
import sys
from pathlib import Path

from pyatmo.const import HOME
from pyatmo.helpers import extract_raw_data
from pyatmo.home import Home

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import client

HOME_TOPOLOGY = {
    "id": "home1",
    "name": "Test Home",
    "rooms": [
        {
            "id": "room1",
            "name": "Bathroom",
            "type": "bathroom",
            "module_ids": ["nxs1", "nxo1"],
        },
        {
            "id": "room2",
            "name": "Garage",
            "type": "garage",
            "module_ids": [],
        },
    ],
    "modules": [
        {
            "id": "gateway1",
            "name": "VELUX Gateway",
            "type": "NXG",
        },
        {
            "id": "nxs1",
            "name": "Indoor sensor",
            "type": "NXS",
            "room_id": "room1",
            "bridge": "gateway1",
        },
        {
            "id": "nxd1",
            "name": "Departure switch",
            "type": "NXD",
            "bridge": "gateway1",
        },
        {
            "id": "nxo1",
            "name": "Roof window",
            "type": "NXO",
            "room_id": "room1",
            "bridge": "gateway1",
            "velux_type": "window",
        },
    ],
}

HOMESTATUS = {
    "body": {
        "home": {
            "id": "home1",
            "rooms": [
                {
                    "id": "room1",
                    "temperature": 275,
                    "co2": 523,
                    "humidity": 71,
                    "lux": 0,
                    "air_quality": 4,
                    "algo_status": 114,
                    "auto_close_ts": 0,
                    "min_comfort_temperature": 170,
                    "max_comfort_temperature": 260,
                    "min_comfort_humidity": 20,
                    "max_comfort_humidity": 70,
                    "max_comfort_co2": 1150,
                },
                {"id": "room2"},
            ],
            "modules": [
                {
                    "id": "nxs1",
                    "type": "NXS",
                    "battery_level": 3908,
                    "battery_percent": 59,
                    "battery_state": "medium",
                    "firmware_revision": 16,
                    "last_seen": 1783156874,
                    "reachable": True,
                    "rf_state": "full",
                    "rf_strength": 50,
                },
                {
                    "id": "nxd1",
                    "type": "NXD",
                    "battery_percent": 72,
                    "battery_state": "high",
                },
                {
                    "id": "nxo1",
                    "type": "NXO",
                    "battery_percent": 100,
                },
            ],
        }
    }
}


def test_serialize_home_includes_raw_room_measurements_and_module_batteries():
    home = Home(None, HOME_TOPOLOGY)
    asyncio.run(home.update(extract_raw_data(HOMESTATUS, HOME)))

    serialized = client.serialize_home(home, HOMESTATUS)

    room = next(item for item in serialized["rooms"] if item["id"] == "room1")
    assert room["temperature"] == 275
    assert room["co2"] == 523
    assert room["humidity"] == 71
    assert room["lux"] == 0
    assert room["air_quality"] == 4
    assert room["algo_status"] == 114
    assert room["auto_close_ts"] == 0
    assert room["min_comfort_temperature"] == 170
    assert "temperature" not in next(
        item for item in serialized["rooms"] if item["id"] == "room2"
    )

    nxs = next(item for item in serialized["modules"] if item["id"] == "nxs1")
    assert nxs["api_type"] == "NXS"
    assert nxs["battery_percent"] == 59
    assert nxs["battery_state"] == "medium"
    assert nxs["last_seen"] == 1783156874
    assert nxs["rf_state"] == "full"
    assert nxs["rf_strength"] == 50

    nxd = next(item for item in serialized["modules"] if item["id"] == "nxd1")
    assert nxd["api_type"] == "NXD"
    assert nxd["battery_percent"] == 72

    nxo = next(item for item in serialized["modules"] if item["id"] == "nxo1")
    assert "api_type" not in nxo
    assert "battery_percent" not in nxo


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")
    print("all passed")
