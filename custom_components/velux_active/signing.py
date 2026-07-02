"""Pure signing/nonce helpers for Velux Active roof-window commands.

Kept free of Home Assistant imports so the crypto and replay logic can be
unit-tested standalone (see tests/test_signing.py).
"""

from __future__ import annotations

import base64
import hashlib
import hmac


def decode_hash_sign_key(hash_sign_key_b64: str) -> bytes:
    """Decode a Hash Sign Key in standard or URL-safe Base64 form."""
    value = hash_sign_key_b64.strip().replace("-", "+").replace("_", "/")
    value += "=" * (-len(value) % 4)
    return base64.b64decode(value, validate=True)


def compute_hash(
    hash_sign_key_b64: str,
    position: int,
    timestamp: int,
    nonce: int,
    device_id: str,
) -> str:
    """Compute the HMAC-SHA512 hash required to sign a window position command.

    Formula:
        msg  = f"target_position{position}{timestamp}{nonce}{device_id}"
        hash = HMAC-SHA512(key=base64decode(HashSignKey), msg=msg)
        result = base64encode(hash).replace('+', '-').replace('/', '_')
    """
    string_to_hash = f"target_position{position}{timestamp}{nonce}{device_id}"
    key = decode_hash_sign_key(hash_sign_key_b64)
    digest = hmac.new(key, string_to_hash.encode("utf-8"), hashlib.sha512).digest()
    result = base64.b64encode(digest).decode("utf-8")
    return result.replace("+", "-").replace("/", "_")


def allocate_nonces(now_ts: int, last_ts: int, last_nonce: int) -> tuple[int, int]:
    """Pick (timestamp, base_nonce) for a new batch, never reusing a pair.

    If the wall clock has not advanced past the last send, reuse its timestamp
    and continue the nonce sequence; otherwise start fresh at nonce 0.
    """
    if now_ts <= last_ts:
        return last_ts, last_nonce + 1
    return now_ts, 0
