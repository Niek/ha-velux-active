#!/usr/bin/env python3
"""Standalone end-to-end VELUX roof-window signing-key retrieval.

Logs in, triggers the gateway's local key-retrieval mode via the cloud API,
waits for you to press the physical gateway button, then reads the signing key
from the gateway's local Netcom listener — the same flow the Home Assistant
config flow runs, without needing HA.

    python3 dev/retrieve-signing-key.py --username you@example.com --host 192.168.1.50

Requires the same deps as the integration (pyatmo, aiohttp, cryptography).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
import types
from pathlib import Path

# Bootstrap a lightweight package so the HA-free modules resolve their relative
# imports without running the real __init__.py (which imports Home Assistant).
_PKG_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "velux_active"
_pkg = types.ModuleType("velux_active")
_pkg.__path__ = [str(_PKG_DIR)]
sys.modules.setdefault("velux_active", _pkg)


def _gateways(raw: dict) -> list[tuple[str, str, str]]:
    """Return [(home_id, gateway_id, label)] for every NXG gateway."""
    result = []
    for home in raw.get("body", {}).get("homes", []):
        home_id = str(home.get("id") or "")
        home_name = str(home.get("name") or home_id)
        for module in home.get("modules", []):
            if module.get("type") == "NXG":
                gw_id = str(module.get("id") or "")
                gw_name = str(module.get("name") or gw_id)
                result.append((home_id, gw_id, f"{home_name} / {gw_name}"))
    return result


async def _run(args: argparse.Namespace) -> int:
    # Imported lazily so --help works without the integration's runtime deps.
    try:
        import aiohttp
        from velux_active import api, pairing
    except ImportError as err:
        print(
            f"Missing dependency: {err}. Install pyatmo, aiohttp, cryptography.",
            file=sys.stderr,
        )
        return 2

    password = args.password or getpass.getpass("VELUX password: ")
    async with aiohttp.ClientSession() as session:
        client = api.VeluxActiveClient(session, args.username, password)
        try:
            raw = await client.async_get_raw_homesdata()
        except api.VeluxActiveError as err:
            print(f"Login/homesdata failed: {err}", file=sys.stderr)
            return 1

        gateways = _gateways(raw)
        if not gateways:
            print("No VELUX gateway (NXG) found on this account.", file=sys.stderr)
            return 1
        if args.gateway:
            match = [g for g in gateways if g[1] == args.gateway]
            if not match:
                print(f"Gateway {args.gateway} not found. Available:", file=sys.stderr)
                for _, gw_id, label in gateways:
                    print(f"  {gw_id}  {label}", file=sys.stderr)
                return 1
            home_id, gateway_id, label = match[0]
        elif len(gateways) == 1:
            home_id, gateway_id, label = gateways[0]
        else:
            print(
                "Multiple gateways found; pick one with --gateway <id>:",
                file=sys.stderr,
            )
            for _, gw_id, label in gateways:
                print(f"  {gw_id}  {label}", file=sys.stderr)
            return 1

        print(f"Triggering key retrieval on {label} ({gateway_id})...")
        try:
            await client.async_trigger_retrieve_key(home_id, gateway_id)
        except api.VeluxActiveError as err:
            print(f"retrieve_key trigger failed: {err}", file=sys.stderr)
            return 1

        input(
            "Wait for the gateway LED to flash, press the gateway button, then Enter... "
        )
        try:
            key = await asyncio.to_thread(
                pairing.retrieve_signing_key, host=args.host, timeout=args.timeout
            )
        except pairing.VeluxPairingError as err:
            print(f"Local key retrieval failed: {err}", file=sys.stderr)
            return 1

    print(f"sign_key_id={key.sign_key_id}")
    print(f"hash_sign_key={key.hash_sign_key}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True, help="VELUX ACTIVE account email")
    parser.add_argument("--password", help="Account password (prompted if omitted)")
    parser.add_argument("--host", required=True, help="Gateway IP address or hostname")
    parser.add_argument(
        "--gateway", help="Gateway module id (for multi-gateway accounts)"
    )
    parser.add_argument(
        "--timeout", default=30, type=int, help="Seconds to wait for the local listener"
    )
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
