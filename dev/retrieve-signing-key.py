#!/usr/bin/env python3
"""Retrieve VELUX roof-window signing keys from a gateway in pairing mode."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys

PAIRING_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "velux_active"
    / "pairing.py"
)

spec = importlib.util.spec_from_file_location("velux_active_pairing", PAIRING_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Could not load pairing helper from {PAIRING_PATH}")
pairing = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pairing
spec.loader.exec_module(pairing)


def main() -> int:
    """Run the standalone local Netcom key retrieval helper."""
    parser = argparse.ArgumentParser(
        description=(
            "Retrieve signing keys from a VELUX gateway whose local retrieve-key "
            "listener is already active. Trigger pairing from Home Assistant or "
            "another client first, wait for the gateway LED to flash, then press "
            "the gateway button before running this helper."
        )
    )
    parser.add_argument("--host", required=True, help="Gateway IP address or hostname")
    parser.add_argument(
        "--timeout",
        default=30,
        type=int,
        help="Seconds to wait for the local listener to appear",
    )
    args = parser.parse_args()

    try:
        signing_key = pairing.retrieve_signing_key(host=args.host, timeout=args.timeout)
    except pairing.VeluxPairingError as err:
        print(f"Pairing failed: {err}", file=sys.stderr)
        return 1

    print(f"sign_key_id={signing_key.sign_key_id}")
    print(f"hash_sign_key={signing_key.hash_sign_key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
