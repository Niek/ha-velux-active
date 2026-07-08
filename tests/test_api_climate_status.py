"""Standalone checks for VELUX raw homestatus parsing.

Run: uv run --with aiohttp --with pyatmo==9.4.0 python tests/test_api_climate_status.py
"""

import sys
import types
from pathlib import Path

# Bootstrap a stub package so api.py's relative imports resolve without HA.
_PKG = Path(__file__).resolve().parents[1] / "custom_components" / "velux_active"
_pkg = types.ModuleType("velux_active")
_pkg.__path__ = [str(_PKG)]
sys.modules.setdefault("velux_active", _pkg)

from velux_active.api import _extract_climate_status  # noqa: E402


class FakeRoom:
    def __init__(self, name, module_ids):
        self.name = name
        self.modules = {module_id: object() for module_id in module_ids}


class FakeModule:
    def __init__(self, name, room_id):
        self.name = name
        self.room_id = room_id


class FakeHome:
    def __init__(self):
        self.rooms = {
            "room1": FakeRoom("Bathroom", ["nxs1", "nxo1"]),
            "room2": FakeRoom("Bedroom", ["nxs2", "nxs3"]),
        }
        self.modules = {
            "nxs1": FakeModule("Indoor sensor", "room1"),
            "nxs2": FakeModule("Left indoor sensor", "room2"),
            "nxs3": FakeModule("Right indoor sensor", "room2"),
            "nxd1": FakeModule("Departure switch", None),
            "nxo1": FakeModule("Roof window", "room1"),
        }


def test_extract_climate_status_keeps_room_measurements_separate_from_modules():
    raw_status = {
        "body": {
            "home": {
                "rooms": [
                    {
                        "id": "room1",
                        "temperature": 275,
                        "co2": 523,
                        "humidity": 71,
                        "lux": 0,
                        "air_quality": 4,
                    },
                    {
                        "id": "room2",
                        "temperature": 261,
                        "co2": 612,
                    },
                ],
                "modules": [
                    {
                        "id": "nxs1",
                        "type": "NXS",
                        "battery_level": 3908,
                        "battery_percent": 59,
                        "battery_state": "medium",
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
                        "id": "nxs2",
                        "type": "NXS",
                        "battery_percent": 45,
                    },
                    {
                        "id": "nxs3",
                        "type": "NXS",
                        "battery_percent": 52,
                    },
                    {"id": "nxo1", "type": "NXO", "battery_percent": 100},
                ],
            }
        }
    }

    rooms, sensor_modules = _extract_climate_status(
        {"HOME_1": FakeHome()}, {"HOME_1": raw_status}
    )

    assert set(rooms) == {"HOME_1:room1", "HOME_1:room2"}
    assert rooms["HOME_1:room1"]["name"] == "Bathroom"
    assert rooms["HOME_1:room1"]["module_ids"] == ["nxs1", "nxo1"]
    assert rooms["HOME_1:room1"]["temperature"] == 275
    assert rooms["HOME_1:room1"]["co2"] == 523
    assert rooms["HOME_1:room2"]["name"] == "Bedroom"
    assert rooms["HOME_1:room2"]["module_ids"] == ["nxs2", "nxs3"]
    assert rooms["HOME_1:room2"]["temperature"] == 261

    assert set(sensor_modules) == {"nxs1", "nxd1", "nxs2", "nxs3"}
    assert sensor_modules["nxs1"]["name"] == "Indoor sensor"
    assert sensor_modules["nxs1"]["room_id"] == "room1"
    assert sensor_modules["nxs1"]["room_name"] == "Bathroom"
    assert "temperature" not in sensor_modules["nxs1"]
    assert sensor_modules["nxs1"]["battery_percent"] == 59
    assert sensor_modules["nxs2"]["room_name"] == "Bedroom"
    assert sensor_modules["nxd1"]["name"] == "Departure switch"
    assert sensor_modules["nxd1"]["room_id"] is None


def test_extract_climate_status_keeps_room_measurements_without_nxs_module():
    raw_status = {
        "body": {
            "home": {
                "rooms": [
                    {
                        "id": "room1",
                        "temperature": 275,
                        "co2": 523,
                    },
                ],
                "modules": [
                    {
                        "id": "nxd1",
                        "type": "NXD",
                        "battery_percent": 72,
                    },
                ],
            }
        }
    }

    rooms, sensor_modules = _extract_climate_status(
        {"HOME_1": FakeHome()}, {"HOME_1": raw_status}
    )

    assert set(rooms) == {"HOME_1:room1"}
    assert rooms["HOME_1:room1"]["temperature"] == 275
    assert set(sensor_modules) == {"nxd1"}
    assert "temperature" not in sensor_modules["nxd1"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")
    print("all passed")
