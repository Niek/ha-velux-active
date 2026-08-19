"""Tests for realtime cover-state merging."""

from types import SimpleNamespace

from velux_active.api import VeluxActiveClient


class FakeCover:
    """Small duck-typed pyatmo cover."""

    def __init__(self):
        self.current_position = 100
        self.target_position = 100
        self.reachable = True
        self.mode = "manual"
        self.last_seen = 100
        self.bridge = "gateway1"
        self.velux_type = "awning_blind"

    async def async_set_target_position(self, position):
        return True


def make_client(cover):
    client = object.__new__(VeluxActiveClient)
    client._account = SimpleNamespace(
        homes={"home1": SimpleNamespace(modules={"cover1": cover})}
    )
    return client


def test_realtime_event_updates_only_cover_state_fields():
    cover = FakeCover()
    client = make_client(cover)

    changed = client.apply_realtime_cover_event(
        {
            "timestamp": 200,
            "correlation_id": "correlation1",
            "home": {
                "id": "home1",
                "modules": [
                    {
                        "id": "cover1",
                        "current_position": 100,
                        "target_position": 0,
                        "reachable": True,
                        "mode": "algo_disabled",
                        "last_seen": 200,
                        "bridge": "changed-gateway",
                        "velux_type": "changed-type",
                    }
                ],
            },
        }
    )

    assert changed is True
    assert cover.current_position == 100
    assert cover.target_position == 0
    assert cover.reachable is True
    assert cover.mode == "algo_disabled"
    assert cover.last_seen == 200
    assert cover.bridge == "gateway1"
    assert cover.velux_type == "awning_blind"


def test_realtime_event_ignores_stale_cover_state():
    cover = FakeCover()
    client = make_client(cover)

    changed = client.apply_realtime_cover_event(
        {
            "timestamp": 99,
            "home": {
                "id": "home1",
                "modules": [
                    {
                        "id": "cover1",
                        "current_position": 0,
                        "target_position": 0,
                    }
                ],
            },
        }
    )

    assert changed is False
    assert cover.current_position == 100
    assert cover.target_position == 100
    assert cover.last_seen == 100


def test_realtime_event_ignores_unknown_or_malformed_payloads():
    client = make_client(FakeCover())

    assert client.apply_realtime_cover_event({}) is False
    assert (
        client.apply_realtime_cover_event({"home": {"id": "unknown", "modules": []}})
        is False
    )
    assert (
        client.apply_realtime_cover_event(
            {"home": {"id": "home1", "modules": [{"id": "unknown"}]}}
        )
        is False
    )
