"""Pure helpers for Velux Active roof-window commands.

Signing, nonce allocation, gateway routing, payload assembly and response
parsing — kept free of Home Assistant imports so the risky logic can be
unit-tested standalone (see tests/test_signing.py).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Any


def decode_hash_sign_key(hash_sign_key_b64: str) -> bytes:
    """Decode a Hash Sign Key in standard or URL-safe Base64 form."""
    value = hash_sign_key_b64.strip().replace("-", "+").replace("_", "/")
    value += "=" * (-len(value) % 4)
    return base64.b64decode(value, validate=True)


def _sign(hash_sign_key_b64: str, message: str) -> str:
    """HMAC-SHA512 the message with the decoded key, URL-safe Base64 encoded."""
    key = decode_hash_sign_key(hash_sign_key_b64)
    digest = hmac.new(key, message.encode("utf-8"), hashlib.sha512).digest()
    return base64.b64encode(digest).decode("utf-8").replace("+", "-").replace("/", "_")


def compute_hash(
    hash_sign_key_b64: str,
    position: int,
    timestamp: int,
    nonce: int,
    device_id: str,
) -> str:
    """HMAC for a window position command: msg = target_position{...}."""
    return _sign(
        hash_sign_key_b64,
        f"target_position{position}{timestamp}{nonce}{device_id}",
    )


def compute_scenario_hash(
    hash_sign_key_b64: str,
    scenario: str,
    timestamp: int,
    nonce: int,
    bridge_id: str,
) -> str:
    """HMAC for a gateway scenario command: msg = scenario{...}."""
    return _sign(
        hash_sign_key_b64,
        f"scenario{scenario}{timestamp}{nonce}{bridge_id}",
    )


def allocate_nonces(now_ts: int, last_ts: int, last_nonce: int) -> tuple[int, int]:
    """Pick (timestamp, base_nonce) for a new batch, never reusing a pair.

    If the wall clock has not advanced past the last send, reuse its timestamp
    and continue the nonce sequence; otherwise start fresh at nonce 0.
    """
    if now_ts <= last_ts:
        return last_ts, last_nonce + 1
    return now_ts, 0


def resolve_bridge_id(module_bridge: str | None, nxg_ids: list[str]) -> str | None:
    """Pick the gateway for a window: its own bridge link, else the home's sole
    gateway. Returns None if that would mean guessing between several."""
    if module_bridge and module_bridge in nxg_ids:
        return module_bridge
    if len(nxg_ids) == 1:
        return nxg_ids[0]
    return None


def should_force_rain_override(
    is_raining: bool | None,
    current_position: int | None,
    target_position: int,
) -> bool:
    """Return whether a rainy roof-window opening needs the API override."""
    current_position = current_position if current_position is not None else 0
    return is_raining is True and target_position > current_position


def build_signed_modules(
    commands: list[dict],
    timestamp: int,
    base_nonce: int,
    bridge_id: str,
    sign_key_id: str,
    hash_sign_key: str,
) -> list[dict]:
    """Build the signed per-window module payloads for a setstate batch.

    Each command is {"id": module_id, "position": raw_position} with an
    optional true "force" flag; nonces are assigned sequentially from
    base_nonce.
    """
    modules = []
    for offset, cmd in enumerate(commands):
        nonce = base_nonce + offset
        module = {
            "id": cmd["id"],
            "nonce": nonce,
            "bridge": bridge_id,
            "sign_key_id": sign_key_id,
            "target_position": cmd["position"],
            "hash_target_position": compute_hash(
                hash_sign_key, cmd["position"], timestamp, nonce, cmd["id"]
            ),
            "timestamp": timestamp,
        }
        if cmd.get("force"):
            module["force"] = True
        modules.append(module)
    return modules


def retrieve_key_error(ok: bool, status: int, raw: Any) -> str | None:
    """Return an error message if a retrieve_key response failed, else None.

    The API can return HTTP 200 with a product-level ``body.errors`` list, so
    inspecting the status alone is not enough.
    """
    if not ok:
        return f"retrieve_key request failed with status {status}"
    body = raw.get("body") if isinstance(raw, dict) else None
    errors = body.get("errors") if isinstance(body, dict) else None
    if errors:
        return f"gateway rejected key retrieval: {errors}"
    return None
