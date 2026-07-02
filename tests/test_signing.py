"""Standalone checks for the signing/nonce logic. Run: python3 tests/test_signing.py"""

import sys
from pathlib import Path

# Import signing.py directly — the package __init__ imports Home Assistant.
sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "custom_components" / "velux_active")
)

from signing import allocate_nonces, compute_hash  # noqa: E402

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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok {name}")
    print("all passed")
