"""Standalone checks for the signing/nonce logic. Run: python3 tests/test_signing.py"""

import sys
from pathlib import Path

# Import signing.py directly — the package __init__ imports Home Assistant.
sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "custom_components" / "velux_active")
)

from signing import (  # noqa: E402
    allocate_nonces,
    build_signed_modules,
    compute_hash,
    compute_scenario_hash,
    resolve_bridge_id,
    retrieve_key_error,
    should_force_rain_override,
)

KEY = "AAAAAAAAAAAAAAAAAAAAAA=="  # 16 zero bytes, base64


def _emulate_batches(clock, batch_sizes):
    """Replay how cover.py allocates nonces and collect every (ts, nonce) pair."""
    last_ts, last_nonce = 0, -1
    pairs = []
    for now, count in zip(clock, batch_sizes):
        ts, base = allocate_nonces(now, last_ts, last_nonce)
        pairs.extend((ts, base + i) for i in range(count))
        last_ts, last_nonce = ts, base + count - 1
    return pairs


def test_nonce_never_reused_within_same_second():
    # Three batches all reported at t=100 -> must not repeat any (ts, nonce).
    pairs = _emulate_batches([100, 100, 100], [1, 1, 2])
    assert pairs == [(100, 0), (100, 1), (100, 2), (100, 3)], pairs
    assert len(set(pairs)) == len(pairs)


def test_nonce_resets_when_clock_advances():
    assert allocate_nonces(200, 100, 5) == (200, 0)


def test_nonce_holds_timestamp_when_clock_stalls():
    assert allocate_nonces(100, 100, 5) == (100, 6)
    assert allocate_nonces(99, 100, 5) == (100, 6)  # clock went backwards


def test_hash_is_deterministic_and_url_safe():
    h1 = compute_hash(KEY, 100, 12345, 0, "dev1")
    h2 = compute_hash(KEY, 100, 12345, 0, "dev1")
    assert h1 == h2
    assert "+" not in h1 and "/" not in h1


def test_hash_changes_with_each_signed_field():
    base = compute_hash(KEY, 100, 12345, 0, "dev1")
    assert compute_hash(KEY, 50, 12345, 0, "dev1") != base   # position
    assert compute_hash(KEY, 100, 99999, 0, "dev1") != base  # timestamp
    assert compute_hash(KEY, 100, 12345, 1, "dev1") != base  # nonce
    assert compute_hash(KEY, 100, 12345, 0, "dev2") != base  # device


def test_resolve_bridge_prefers_module_link():
    assert resolve_bridge_id("gwA", ["gwA", "gwB"]) == "gwA"


def test_resolve_bridge_falls_back_only_when_unambiguous():
    assert resolve_bridge_id(None, ["gwA"]) == "gwA"        # single gateway
    assert resolve_bridge_id(None, ["gwA", "gwB"]) is None  # never guess
    assert resolve_bridge_id("stale", ["gwA", "gwB"]) is None
    assert resolve_bridge_id(None, []) is None


def test_build_signed_modules_assigns_sequential_nonces_and_signs():
    cmds = [
        {"id": "m1", "position": 100, "force": True},
        {"id": "m2", "position": 50},
    ]
    mods = build_signed_modules(cmds, 12345, 3, "bridgeX", "kid", KEY)
    assert [m["nonce"] for m in mods] == [3, 4]
    assert [m["bridge"] for m in mods] == ["bridgeX", "bridgeX"]
    assert mods[0]["sign_key_id"] == "kid"
    assert mods[0]["target_position"] == 100
    assert mods[0]["hash_target_position"] == compute_hash(KEY, 100, 12345, 3, "m1")
    assert mods[0]["force"] is True
    assert "force" not in mods[1]


def test_rain_override_only_applies_to_opening_moves():
    assert should_force_rain_override(True, 0, 50)
    assert should_force_rain_override(True, None, 50)
    assert not should_force_rain_override(True, 50, 50)
    assert not should_force_rain_override(True, 100, 50)
    assert not should_force_rain_override(False, 0, 50)
    assert not should_force_rain_override(None, 0, 50)
    assert not should_force_rain_override(True, None, 0)


def test_scenario_hash_deterministic_urlsafe_and_field_sensitive():
    base = compute_scenario_hash(KEY, "home", 12345, 0, "bridge1")
    assert base == compute_scenario_hash(KEY, "home", 12345, 0, "bridge1")
    assert "+" not in base and "/" not in base
    assert compute_scenario_hash(KEY, "away", 12345, 0, "bridge1") != base  # scenario
    assert compute_scenario_hash(KEY, "home", 99999, 0, "bridge1") != base  # timestamp
    assert compute_scenario_hash(KEY, "home", 12345, 1, "bridge1") != base  # nonce
    assert compute_scenario_hash(KEY, "home", 12345, 0, "bridge2") != base  # bridge


def test_scenario_hash_differs_from_position_hash():
    # Different message prefix -> must not collide with a window position hash.
    assert compute_scenario_hash(KEY, "home", 1, 0, "x") != compute_hash(KEY, 0, 1, 0, "x")


def test_retrieve_key_error_flags_http_and_body_errors():
    assert retrieve_key_error(True, 200, {"body": {}}) is None
    assert retrieve_key_error(False, 500, {}) is not None            # HTTP failure
    assert retrieve_key_error(True, 200, {"body": {"errors": [1]}})  # 200 + body.errors
    assert retrieve_key_error(True, 200, "garbage") is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")
    print("all passed")
