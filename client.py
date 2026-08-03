#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "aiohttp>=3.7.4,<4.0.0",
#   "cryptography>=42.0.0",
#   "pyatmo==9.4.0",
# ]
# ///
"""Minimal VELUX ACTIVE CLI backed by upstream pyatmo."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
VELUX_ACTIVE_SRC = ROOT / "custom_components" / "velux_active"
if str(VELUX_ACTIVE_SRC) not in sys.path:
    sys.path.insert(0, str(VELUX_ACTIVE_SRC))

import aiohttp
import pyatmo
from connectivity import gateway_reachable
from pairing import SigningKey, retrieve_signing_key
from pyatmo.const import (
    AUTH_REQ_ENDPOINT,
    DEFAULT_BASE_URL,
    GETHOMESDATA_ENDPOINT,
    GETHOMESTATUS_ENDPOINT,
    HOME,
    SETSTATE_ENDPOINT,
)
from pyatmo.enums import ScheduleType
from pyatmo.exceptions import NoDeviceError
from pyatmo.helpers import extract_raw_data
from pyatmo.modules.device_types import DeviceType
from signing import (
    allocate_nonces,
    build_signed_modules,
    resolve_bridge_id,
    retrieve_key_error,
)

# Work around pyatmo 9.4.0 until https://github.com/jabesq-org/pyatmo/pull/564 is released.
ScheduleType._value2member_map_.setdefault("algo", ScheduleType.AUTO)

DEFAULT_CLIENT_ID = "5931426da127d981e76bdd3f"
DEFAULT_CLIENT_SECRET = "6ae2d89d15e767ae5c56b456b452d319"
DEFAULT_APP_VERSION = "791302006"
DEFAULT_USER_PREFIX = "velux"
DEFAULT_SCOPE = "velux_scopes"
DEFAULT_TIMEOUT = 10.0
DEFAULT_SYNC_BASE_URL = "https://app.velux-active.com"
DEFAULT_APP_TYPE = "app_velux"
DEFAULT_TIMEZONE = "UTC"
BATTERY_MODULE_TYPES = frozenset({"NXS", "NXD"})
CONTROLLED_OPENERS_OPTIONS = ("windows", "external_covers")
ROOM_MEASUREMENT_KEYS = (
    "temperature",
    "co2",
    "humidity",
    "lux",
    "air_quality",
    "algo_status",
    "auto_close_ts",
    "min_comfort_temperature",
    "max_comfort_temperature",
    "min_comfort_humidity",
    "max_comfort_humidity",
    "max_comfort_co2",
)
MODULE_STATUS_KEYS = (
    "type",
    "battery_level",
    "battery_percent",
    "battery_state",
    "reachable",
    "last_seen",
    "rf_state",
    "rf_strength",
    "firmware_revision",
)


class VeluxAuthError(RuntimeError):
    """Raised when VELUX authentication fails."""


@dataclass(slots=True)
class OAuthTokens:
    """Container for OAuth token data."""

    access_token: str
    refresh_token: str | None
    expires_in: int | None
    expires_at: int | None
    issued_at: int
    scope: list[str]
    token_type: str | None
    raw: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """Serialize tokens for CLI output."""

        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_in": self.expires_in,
            "expires_at": self.expires_at,
            "scope": self.scope,
            "token_type": self.token_type,
            "issued_at": self.issued_at,
            "raw": self.raw,
        }


class VeluxAsyncAuth(pyatmo.AbstractAsyncAuth):
    """pyatmo auth adapter for the VELUX ACTIVE password grant."""

    def __init__(
        self,
        websession: aiohttp.ClientSession,
        *,
        base_url: str,
        client_id: str,
        client_secret: str,
        app_version: str,
        user_prefix: str,
        scope: str,
        timeout: float,
        username: str | None = None,
        password: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        expires_at: int | None = None,
    ) -> None:
        """Initialize auth state."""

        super().__init__(websession, base_url=normalize_base_url(base_url))
        self.client_id = client_id
        self.client_secret = client_secret
        self.app_version = app_version
        self.user_prefix = user_prefix
        self.scope = scope
        self.timeout = timeout
        self.username = username
        self.password = password
        self._tokens: OAuthTokens | None = None

        if access_token is not None:
            issued_at = int(time.time())
            self._tokens = OAuthTokens(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in=(expires_at - issued_at) if expires_at is not None else None,
                expires_at=expires_at,
                issued_at=issued_at,
                scope=(scope.split() if scope else []),
                token_type=None,
                raw={},
            )
        elif refresh_token is not None:
            issued_at = int(time.time())
            self._tokens = OAuthTokens(
                access_token="",
                refresh_token=refresh_token,
                expires_in=None,
                expires_at=expires_at,
                issued_at=issued_at,
                scope=(scope.split() if scope else []),
                token_type=None,
                raw={},
            )

    async def async_get_access_token(self) -> str:
        """Return a valid access token for pyatmo requests."""

        if self._tokens is None:
            await self.login()
        elif self._tokens.access_token and not self._token_expired(self._tokens):
            return self._tokens.access_token
        elif self._tokens.refresh_token:
            try:
                await self.refresh()
            except VeluxAuthError:
                if self.username and self.password:
                    await self.login()
                else:
                    raise
        else:
            await self.login()

        if self._tokens is None or not self._tokens.access_token:
            raise VeluxAuthError("No access token available")
        return self._tokens.access_token

    async def login(self) -> OAuthTokens:
        """Authenticate with username and password."""

        if not self.username or not self.password:
            raise VeluxAuthError("Email and password are required for login")

        return await self._request_tokens(
            {
                "grant_type": "password",
                "username": self.username,
                "password": self.password,
            },
        )

    async def refresh(self) -> OAuthTokens:
        """Refresh the access token."""

        refresh_token = self._tokens.refresh_token if self._tokens else None
        if not refresh_token:
            raise VeluxAuthError("Refresh token is not available")

        return await self._request_tokens(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )

    async def _request_tokens(self, payload: dict[str, str]) -> OAuthTokens:
        """Request tokens from the OAuth endpoint."""

        url = self.base_url + AUTH_REQ_ENDPOINT
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "app_version": self.app_version,
            **payload,
        }
        if payload.get("grant_type") == "password":
            data["user_prefix"] = self.user_prefix
            data["scope"] = self.scope

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with self.websession.post(url, data=data, timeout=timeout) as resp:
            try:
                raw: Any = await resp.json(content_type=None)
            except (aiohttp.ContentTypeError, json.JSONDecodeError):
                raw = {"raw": await resp.text()}

        if not resp.ok:
            raise VeluxAuthError(format_auth_error(resp.status, raw))

        if not isinstance(raw, dict) or "access_token" not in raw:
            raise VeluxAuthError(f"Unexpected token response from {url}")

        tokens = parse_tokens(raw)
        self._tokens = tokens
        return tokens

    async def process_response(
        self,
        response: aiohttp.ClientResponse,
        url: str,
    ) -> aiohttp.ClientResponse:
        """Process API responses and fail on product-level setstate errors."""

        response = await super().process_response(response, url)
        if not url.endswith(SETSTATE_ENDPOINT):
            return response

        try:
            raw: Any = await response.json(content_type=None)
        except (aiohttp.ContentTypeError, json.JSONDecodeError):
            return response

        body = raw.get("body") if isinstance(raw, dict) else None
        errors = body.get("errors") if isinstance(body, dict) else None
        if errors:
            raise RuntimeError(f"setstate returned API errors: {errors}")

        return response

    @staticmethod
    def _token_expired(tokens: OAuthTokens) -> bool:
        """Return whether the token is expired or close to expiry."""

        return tokens.expires_at is not None and int(time.time()) >= max(
            tokens.expires_at - 60, tokens.issued_at
        )


def normalize_base_url(value: str) -> str:
    """Normalize the base URL to include a trailing slash."""

    return value if value.endswith("/") else f"{value}/"


def parse_tokens(raw: dict[str, Any]) -> OAuthTokens:
    """Parse a token response."""

    issued_at = int(time.time())
    expires_in_raw = raw.get("expires_in", raw.get("expire_in"))
    expires_in = int(expires_in_raw) if expires_in_raw is not None else None
    expires_at = issued_at + expires_in if expires_in is not None else None

    scope_raw = raw.get("scope", [])
    if isinstance(scope_raw, str):
        scope = [part for part in scope_raw.split() if part]
    elif isinstance(scope_raw, list):
        scope = [str(part) for part in scope_raw]
    else:
        scope = []

    refresh_token = raw.get("refresh_token")
    if refresh_token is not None:
        refresh_token = str(refresh_token)

    return OAuthTokens(
        access_token=str(raw["access_token"]),
        refresh_token=refresh_token,
        expires_in=expires_in,
        expires_at=expires_at,
        issued_at=issued_at,
        scope=scope,
        token_type=raw.get("token_type"),
        raw=raw,
    )


def format_auth_error(status: int, raw: Any) -> str:
    """Format an auth error payload."""

    if isinstance(raw, dict):
        error = raw.get("error")
        description = raw.get("error_description")

        if isinstance(error, dict):
            message = error.get("message") or json.dumps(error, sort_keys=True)
        elif error is not None:
            message = str(error)
        else:
            message = json.dumps(raw, sort_keys=True)

        if description:
            return f"{status} - {message}: {description}"
        return f"{status} - {message}"

    return f"{status} - {raw}"


def add_connection_arguments(parser: argparse.ArgumentParser) -> None:
    """Add connection arguments."""

    parser.add_argument(
        "--base-url",
        default=os.getenv("VELUX_BASE_URL", DEFAULT_BASE_URL),
        help=f"API base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--sync-base-url",
        default=os.getenv("VELUX_SYNC_BASE_URL", DEFAULT_SYNC_BASE_URL),
        help=f"VELUX app sync API base URL (default: {DEFAULT_SYNC_BASE_URL})",
    )
    parser.add_argument(
        "--client-id",
        default=os.getenv("VELUX_CLIENT_ID", DEFAULT_CLIENT_ID),
        help="OAuth client ID",
    )
    parser.add_argument(
        "--client-secret",
        default=os.getenv("VELUX_CLIENT_SECRET", DEFAULT_CLIENT_SECRET),
        help="OAuth client secret",
    )
    parser.add_argument(
        "--app-version",
        default=os.getenv("VELUX_APP_VERSION", DEFAULT_APP_VERSION),
        help="VELUX app version value",
    )
    parser.add_argument(
        "--user-prefix",
        default=os.getenv("VELUX_USER_PREFIX", DEFAULT_USER_PREFIX),
        help="VELUX user prefix",
    )
    parser.add_argument(
        "--scope",
        default=os.getenv("VELUX_SCOPE", DEFAULT_SCOPE),
        help="OAuth scope",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("VELUX_TIMEOUT", DEFAULT_TIMEOUT)),
        help=f"Request timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--timezone",
        default=os.getenv("VELUX_TIMEZONE", DEFAULT_TIMEZONE),
        help=f"Home timezone for sync setstate payloads (default: {DEFAULT_TIMEZONE})",
    )


def add_auth_arguments(parser: argparse.ArgumentParser) -> None:
    """Add credential arguments."""

    parser.add_argument(
        "--email",
        default=os.getenv("VELUX_EMAIL"),
        help="VELUX account email",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("VELUX_PASSWORD"),
        help="VELUX account password",
    )
    parser.add_argument(
        "--access-token",
        default=os.getenv("VELUX_ACCESS_TOKEN"),
        help="Existing access token",
    )
    parser.add_argument(
        "--refresh-token",
        default=os.getenv("VELUX_REFRESH_TOKEN"),
        help="Existing refresh token",
    )
    parser.add_argument(
        "--expires-at",
        type=int,
        default=parse_optional_int(os.getenv("VELUX_EXPIRES_AT")),
        help="Unix timestamp for access token expiry",
    )


def add_signing_arguments(parser: argparse.ArgumentParser) -> None:
    """Add signing key arguments for roof-window commands."""

    parser.add_argument(
        "--hash-sign-key",
        default=os.getenv("VELUX_HASH_SIGN_KEY"),
        help="Hash Sign Key returned by retrieve-key",
    )
    parser.add_argument(
        "--sign-key-id",
        default=os.getenv("VELUX_SIGN_KEY_ID"),
        help="Sign Key ID returned by retrieve-key",
    )
    parser.add_argument(
        "--sign-key-gateway",
        default=os.getenv("VELUX_SIGN_KEY_GATEWAY_ID"),
        help="Gateway ID the signing key was paired with",
    )


def parse_optional_int(value: str | None) -> int | None:
    """Parse an optional integer value."""

    return int(value) if value else None


def build_parser() -> argparse.ArgumentParser:
    """Build the command line parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    connection_parent = argparse.ArgumentParser(add_help=False)
    add_connection_arguments(connection_parent)

    auth_parent = argparse.ArgumentParser(add_help=False, parents=[connection_parent])
    add_auth_arguments(auth_parent)

    subparsers = parser.add_subparsers(dest="command", required=True)

    login_parser = subparsers.add_parser(
        "login",
        parents=[connection_parent],
        help="Authenticate and print tokens",
    )
    login_parser.add_argument("login_email", nargs="?", help="VELUX account email")
    login_parser.add_argument(
        "login_password",
        nargs="?",
        help="VELUX account password",
    )

    subparsers.add_parser(
        "list-devices",
        parents=[auth_parent],
        help="List homes and devices",
    )

    subparsers.add_parser(
        "raw-homesdata",
        parents=[auth_parent],
        help="Print raw /homesdata response",
    )

    raw_status_parser = subparsers.add_parser(
        "raw-homestatus",
        parents=[auth_parent],
        help="Print raw /homestatus response",
    )
    raw_status_parser.add_argument(
        "home",
        nargs="?",
        help="Home ID or exact name; omitted only when the account has one home",
    )

    get_configs_parser = subparsers.add_parser(
        "get-configs",
        parents=[auth_parent],
        help="Print raw /syncapi/v1/getconfigs response",
    )
    get_configs_parser.add_argument(
        "home",
        nargs="?",
        help="Home ID or exact name; omitted only when the account has one home",
    )

    set_controlled_openers_parser = subparsers.add_parser(
        "set-controlled-openers",
        parents=[auth_parent],
        help="Set which products an indoor climate sensor controls",
    )
    set_controlled_openers_parser.add_argument(
        "module",
        help="NXS module ID or exact name",
    )
    set_controlled_openers_parser.add_argument(
        "controlled_openers",
        choices=CONTROLLED_OPENERS_OPTIONS,
        help="Products controlled by the indoor climate sensor",
    )

    set_position_parser = subparsers.add_parser(
        "set-cover-position",
        parents=[auth_parent],
        help="Set a cover target position (0-100)",
    )
    set_position_parser.add_argument("cover", help="Cover ID or exact name")
    set_position_parser.add_argument("position", type=int, help="Target position")
    set_position_parser.add_argument(
        "--signed",
        action="store_true",
        help="Use the signed VELUX app command path required by roof windows",
    )
    set_position_parser.add_argument(
        "--gateway",
        help="Gateway ID or exact name to use for the signed command",
    )
    add_signing_arguments(set_position_parser)

    stop_cover_parser = subparsers.add_parser(
        "stop-cover",
        parents=[auth_parent],
        help="Stop one cover through the regular cloud command path",
    )
    stop_cover_parser.add_argument("cover", help="Cover ID or exact name")

    stop_gateway_parser = subparsers.add_parser(
        "stop-gateway",
        parents=[auth_parent],
        help="Stop all movements on a VELUX gateway",
    )
    stop_gateway_parser.add_argument(
        "--gateway",
        help="Gateway ID or exact name when multiple NXG gateways exist",
    )

    mode_parser = subparsers.add_parser(
        "set-window-mode",
        parents=[auth_parent],
        help="Set a roof window auto-ventilation mode",
    )
    mode_parser.add_argument("window", help="Window ID or exact name")
    mode_parser.add_argument(
        "mode",
        choices=["on", "off", "algo_available", "manual"],
        help="Use on/algo_available to enable, off/manual to disable",
    )
    mode_parser.add_argument(
        "--gateway",
        help="Gateway ID or exact name when the window bridge cannot be resolved",
    )

    retrieve_key_parser = subparsers.add_parser(
        "retrieve-key",
        parents=[auth_parent],
        help="Trigger gateway authentication and retrieve a local Netcom key",
    )
    retrieve_key_parser.add_argument("host", help="Gateway IP address or hostname")
    retrieve_key_parser.add_argument(
        "--gateway",
        help="Gateway ID or exact name when multiple NXG gateways exist",
    )
    retrieve_key_parser.add_argument(
        "--no-prompt",
        action="store_true",
        help="Do not pause for the physical gateway button step",
    )

    return parser


async def command_login(args: argparse.Namespace) -> dict[str, Any]:
    """Handle the login command."""

    email = args.login_email or os.getenv("VELUX_EMAIL")
    password = args.login_password or os.getenv("VELUX_PASSWORD")
    if not email or not password:
        raise VeluxAuthError("Email and password are required")

    async with aiohttp.ClientSession() as websession:
        auth = VeluxAsyncAuth(
            websession,
            base_url=args.base_url,
            client_id=args.client_id,
            client_secret=args.client_secret,
            app_version=args.app_version,
            user_prefix=args.user_prefix,
            scope=args.scope,
            timeout=args.timeout,
            username=email,
            password=password,
        )
        tokens = await auth.login()

    return tokens.as_dict()


async def command_list_devices(args: argparse.Namespace) -> dict[str, Any]:
    """Handle the list-devices command."""

    async with aiohttp.ClientSession() as websession:
        auth = build_auth(args, websession)
        account, raw_status_by_home_id = await load_account_with_raw_status(auth)
    return serialize_account(account, raw_status_by_home_id)


async def command_raw_homesdata(args: argparse.Namespace) -> dict[str, Any]:
    """Handle the raw-homesdata command."""

    async with aiohttp.ClientSession() as websession:
        auth = build_auth(args, websession)
        return await post_api_json(auth, GETHOMESDATA_ENDPOINT)


async def command_raw_homestatus(args: argparse.Namespace) -> dict[str, Any]:
    """Handle the raw-homestatus command."""

    async with aiohttp.ClientSession() as websession:
        auth = build_auth(args, websession)
        homesdata = await post_api_json(auth, GETHOMESDATA_ENDPOINT)
        home_id = resolve_home_id(homesdata, args.home)
        return await post_api_json(
            auth,
            GETHOMESTATUS_ENDPOINT,
            params={"home_id": home_id},
        )


async def command_get_configs(args: argparse.Namespace) -> dict[str, Any]:
    """Handle the get-configs command."""

    async with aiohttp.ClientSession() as websession:
        auth = build_auth(args, websession)
        homesdata = await post_api_json(auth, GETHOMESDATA_ENDPOINT)
        home_id = resolve_home_id(homesdata, args.home)
        return await get_sync_configs(auth, args, home_id=home_id)


async def command_set_controlled_openers(args: argparse.Namespace) -> dict[str, Any]:
    """Handle the set-controlled-openers command."""

    async with aiohttp.ClientSession() as websession:
        auth = build_auth(args, websession)
        homesdata = await post_api_json(auth, GETHOMESDATA_ENDPOINT)
        home_id, module_id, bridge_id = resolve_controlled_openers_target(
            homesdata, args.module
        )
        return await set_sync_controlled_openers(
            auth,
            args,
            home_id=home_id,
            module_id=module_id,
            bridge_id=bridge_id,
            controlled_openers=args.controlled_openers,
        )


async def command_set_cover_position(args: argparse.Namespace) -> dict[str, Any]:
    """Handle the set-cover-position command."""

    if not 0 <= args.position <= 100:
        raise ValueError("Position must be between 0 and 100")
    if args.signed:
        require_signing_args(args)

    async with aiohttp.ClientSession() as websession:
        auth = build_auth(args, websession)
        account = await load_account(auth)
        home, module = find_cover(account, args.cover)
        if args.signed:
            bridge_id = resolve_module_bridge_id(account, home, module, args.gateway)
            if args.sign_key_gateway and bridge_id != args.sign_key_gateway:
                raise ValueError(
                    f"Cover is on gateway {bridge_id}, but signing key was paired "
                    f"with gateway {args.sign_key_gateway}"
                )
            response = await send_signed_position_command(
                auth,
                args,
                home_id=home.entity_id,
                module_id=module.entity_id,
                bridge_id=bridge_id,
                position=args.position,
            )
            accepted = True
        else:
            accepted = await module.async_set_target_position(args.position)
            response = None
        await account.async_update_status(home.entity_id)
        updated_home = account.homes[home.entity_id]
        updated_module = updated_home.modules[module.entity_id]

    result: dict[str, Any] = {
        "accepted": accepted,
        "requested_position": args.position,
        "home": {"id": updated_home.entity_id, "name": updated_home.name},
        "device": serialize_module(updated_home, updated_module),
    }
    if response is not None:
        result["setstate_response"] = response
    return result


async def command_stop_cover(args: argparse.Namespace) -> dict[str, Any]:
    """Handle the stop-cover command."""

    async with aiohttp.ClientSession() as websession:
        auth = build_auth(args, websession)
        account = await load_account(auth)
        home, module = find_cover(account, args.cover)
        accepted = await module.async_stop()
        await account.async_update_status(home.entity_id)
        updated_home = account.homes[home.entity_id]
        updated_module = updated_home.modules[module.entity_id]

    return {
        "accepted": accepted,
        "home": {"id": updated_home.entity_id, "name": updated_home.name},
        "device": serialize_module(updated_home, updated_module),
    }


async def command_stop_gateway(args: argparse.Namespace) -> dict[str, Any]:
    """Handle the stop-gateway command."""

    async with aiohttp.ClientSession() as websession:
        auth = build_auth(args, websession)
        account = await load_account(auth)
        home, gateway = find_gateway(account, args.gateway)
        response = await send_sync_setstate(
            auth,
            args,
            home_id=home.entity_id,
            modules=[{"id": gateway.entity_id, "stop_movements": "all"}],
        )
        await account.async_update_status(home.entity_id)
        updated_home = account.homes[home.entity_id]
        updated_gateway = updated_home.modules[gateway.entity_id]

    return {
        "accepted": True,
        "home": {"id": updated_home.entity_id, "name": updated_home.name},
        "gateway": serialize_module(updated_home, updated_gateway),
        "setstate_response": response,
    }


async def command_set_window_mode(args: argparse.Namespace) -> dict[str, Any]:
    """Handle the set-window-mode command."""

    mode = {"on": "algo_available", "off": "manual"}.get(args.mode, args.mode)

    async with aiohttp.ClientSession() as websession:
        auth = build_auth(args, websession)
        account = await load_account(auth)
        home, module = find_cover(account, args.window)
        bridge_id = resolve_module_bridge_id(account, home, module, args.gateway)
        response = await send_sync_setstate(
            auth,
            args,
            home_id=home.entity_id,
            modules=[{"id": module.entity_id, "bridge": bridge_id, "mode": mode}],
        )
        await account.async_update_status(home.entity_id)
        updated_home = account.homes[home.entity_id]
        updated_module = updated_home.modules[module.entity_id]

    return {
        "accepted": True,
        "requested_mode": mode,
        "home": {"id": updated_home.entity_id, "name": updated_home.name},
        "device": serialize_module(updated_home, updated_module),
        "setstate_response": response,
    }


async def command_retrieve_key(args: argparse.Namespace) -> dict[str, Any]:
    """Handle the retrieve-key command."""

    require_interactive_gateway_prompt(args.no_prompt)

    async with aiohttp.ClientSession() as websession:
        auth = build_auth(args, websession)
        account = await load_account(auth)
        home, gateway = find_gateway(account, args.gateway)
        trigger_response = await trigger_gateway_key_retrieval(auth, home, gateway)

        await prompt_for_gateway_button(args.no_prompt, home, gateway)

        key = await asyncio.to_thread(
            retrieve_signing_key,
            host=args.host,
            timeout=int(args.timeout),
            socket_timeout=args.timeout,
        )

    return {
        "cloud_triggered": True,
        "home": {"id": home.entity_id, "name": home.name},
        "gateway": serialize_module(home, gateway),
        "trigger_response": trigger_response,
        "netcom": {
            "host": args.host,
            "port": 25050,
            "verified": True,
            "key": serialize_signing_key(key, gateway.entity_id),
        },
    }


def build_auth(
    args: argparse.Namespace,
    websession: aiohttp.ClientSession,
) -> VeluxAsyncAuth:
    """Build an auth instance from CLI arguments."""

    if not args.email and not args.access_token and not args.refresh_token:
        raise VeluxAuthError(
            "Provide --email/--password or an access token / refresh token",
        )

    return VeluxAsyncAuth(
        websession,
        base_url=args.base_url,
        client_id=args.client_id,
        client_secret=args.client_secret,
        app_version=args.app_version,
        user_prefix=args.user_prefix,
        scope=args.scope,
        timeout=args.timeout,
        username=args.email,
        password=args.password,
        access_token=args.access_token,
        refresh_token=args.refresh_token,
        expires_at=args.expires_at,
    )


async def load_account(auth: VeluxAsyncAuth) -> pyatmo.AsyncAccount:
    """Load homes and device status."""

    account, _raw_status_by_home_id = await load_account_with_raw_status(auth)
    return account


async def load_account_with_raw_status(
    auth: VeluxAsyncAuth,
) -> tuple[pyatmo.AsyncAccount, dict[str, dict[str, Any]]]:
    """Load homes, update status, and keep raw status for debug serialization."""

    account = pyatmo.AsyncAccount(auth)
    raw_status_by_home_id: dict[str, dict[str, Any]] = {}
    await account.async_update_topology()
    for home_id in sorted(account.homes):
        try:
            raw_status = await post_api_json(
                auth,
                GETHOMESTATUS_ENDPOINT,
                params={"home_id": home_id},
            )
            raw_data = extract_raw_data(raw_status, HOME)
            await account.homes[home_id].update(
                raw_data, do_raise_for_reachability_error=True
            )
            raw_status_by_home_id[home_id] = raw_status
        except NoDeviceError:
            pass
    return account, raw_status_by_home_id


async def post_api_json(
    auth: VeluxAsyncAuth,
    endpoint: str,
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """POST to a regular Netatmo API endpoint and return JSON."""

    response = await auth.async_post_api_request(endpoint=endpoint, params=params)
    try:
        raw = await response.json(content_type=None)
    except (aiohttp.ContentTypeError, ValueError) as err:
        raise RuntimeError(f"Unexpected non-JSON response from {endpoint}") from err
    if not isinstance(raw, dict):
        raise RuntimeError(f"Unexpected JSON response from {endpoint}: {raw!r}")
    return raw


async def get_sync_configs(
    auth: VeluxAsyncAuth,
    args: argparse.Namespace,
    *,
    home_id: str,
) -> dict[str, Any]:
    """GET the raw VELUX app sync configuration for one home."""

    return await request_sync_json(
        auth,
        args,
        method="GET",
        endpoint="getconfigs",
        params={"home_id": home_id},
    )


async def set_sync_controlled_openers(
    auth: VeluxAsyncAuth,
    args: argparse.Namespace,
    *,
    home_id: str,
    module_id: str,
    bridge_id: str,
    controlled_openers: str,
) -> dict[str, Any]:
    """POST one indoor climate sensor control target to setconfigs."""

    if controlled_openers not in CONTROLLED_OPENERS_OPTIONS:
        raise ValueError(f"Unsupported controlled_openers: {controlled_openers}")

    return await request_sync_json(
        auth,
        args,
        method="POST",
        endpoint="setconfigs",
        json_payload={
            "home_id": home_id,
            "home": {
                "modules": [
                    {
                        "id": module_id,
                        "bridge": bridge_id,
                        "controlled_openers": controlled_openers,
                    }
                ]
            },
        },
    )


async def request_sync_json(
    auth: VeluxAsyncAuth,
    args: argparse.Namespace,
    *,
    method: str,
    endpoint: str,
    params: dict[str, str] | None = None,
    json_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send an authenticated VELUX sync API request and return JSON."""

    access_token = await auth.async_get_access_token()
    url = f"{args.sync_base_url.rstrip('/')}/syncapi/v1/{endpoint}"
    timeout = aiohttp.ClientTimeout(total=args.timeout)
    headers = {"Authorization": f"Bearer {access_token}"}
    request_kwargs: dict[str, Any] = {"headers": headers, "timeout": timeout}
    if params is not None:
        request_kwargs["params"] = params
    if json_payload is not None:
        headers["Content-Type"] = "application/json"
        request_kwargs["json"] = json_payload

    request = getattr(auth.websession, method.lower())
    async with request(url, **request_kwargs) as response:
        text = await response.text()

    if not text.strip():
        raise RuntimeError(
            f"{endpoint} returned empty response (status {response.status})"
        )

    try:
        raw: Any = json.loads(text)
    except json.JSONDecodeError as err:
        raise RuntimeError(f"{endpoint} returned an invalid JSON response") from err

    if not response.ok:
        raise RuntimeError(f"{endpoint} failed with status {response.status}: {raw}")
    if not isinstance(raw, dict):
        raise RuntimeError(f"Unexpected {endpoint} response: {raw!r}")
    body = raw.get("body")
    errors = body.get("errors") if isinstance(body, dict) else None
    if raw.get("status") != "ok" or errors:
        raise RuntimeError(f"{endpoint} failed: {raw}")
    return raw


async def send_signed_position_command(
    auth: VeluxAsyncAuth,
    args: argparse.Namespace,
    *,
    home_id: str,
    module_id: str,
    bridge_id: str,
    position: int,
) -> dict[str, Any]:
    """Send one signed roof-window target-position command."""

    timestamp, base_nonce = allocate_nonces(int(time.time()), 0, -1)
    modules = build_signed_modules(
        [{"id": module_id, "position": position}],
        timestamp,
        base_nonce,
        bridge_id,
        args.sign_key_id,
        args.hash_sign_key,
    )
    return await send_sync_setstate(auth, args, home_id=home_id, modules=modules)


async def send_sync_setstate(
    auth: VeluxAsyncAuth,
    args: argparse.Namespace,
    *,
    home_id: str,
    modules: list[dict[str, Any]],
) -> dict[str, Any]:
    """Send a VELUX app sync setstate payload and fail on API body errors."""

    payload = build_sync_setstate_payload(
        args,
        home_id=home_id,
        modules=modules,
    )
    access_token = await auth.async_get_access_token()
    url = f"{args.sync_base_url.rstrip('/')}/syncapi/v1/setstate"
    timeout = aiohttp.ClientTimeout(total=args.timeout)
    async with auth.websession.post(
        url,
        json=payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        timeout=timeout,
    ) as response:
        text = await response.text()

    if not text.strip():
        raise RuntimeError(
            f"setstate returned empty response (status {response.status})"
        )

    try:
        raw: Any = json.loads(text)
    except json.JSONDecodeError:
        raw = {"raw": text}

    if not response.ok:
        raise RuntimeError(f"setstate failed with status {response.status}: {raw}")

    body = raw.get("body") if isinstance(raw, dict) else None
    errors = body.get("errors") if isinstance(body, dict) else None
    if errors:
        raise RuntimeError(f"setstate returned API errors: {errors}")

    if not isinstance(raw, dict):
        return {"raw": raw}
    return raw


def build_sync_setstate_payload(
    args: argparse.Namespace,
    *,
    home_id: str,
    modules: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the VELUX app sync setstate payload."""

    return {
        "app_type": DEFAULT_APP_TYPE,
        "app_version": args.app_version,
        "home": {
            "id": home_id,
            "timezone": args.timezone,
            "modules": modules,
        },
    }


def require_signing_args(args: argparse.Namespace) -> None:
    """Require complete signing key arguments."""

    missing = [
        name
        for name in ("hash_sign_key", "sign_key_id")
        if not getattr(args, name, None)
    ]
    if missing:
        options = ", ".join(f"--{name.replace('_', '-')}" for name in missing)
        raise ValueError(f"Signed commands require {options}")


def resolve_home_id(raw_homesdata: dict[str, Any], home_ref: str | None) -> str:
    """Resolve a home ID or exact name from raw /homesdata."""

    body = raw_homesdata.get("body")
    homes = body.get("homes", []) if isinstance(body, dict) else []
    if not isinstance(homes, list) or not homes:
        raise ValueError("No homes found in /homesdata")

    if home_ref is None:
        if len(homes) == 1:
            return str(homes[0]["id"])
        available = [f"{home.get('name')} ({home.get('id')})" for home in homes]
        raise ValueError(f"Multiple homes found. Provide a home: {available}")

    id_matches = [home for home in homes if str(home.get("id")) == home_ref]
    if len(id_matches) == 1:
        return str(id_matches[0]["id"])

    name_matches = [
        home
        for home in homes
        if str(home.get("name") or "").casefold() == home_ref.casefold()
    ]
    if len(name_matches) == 1:
        return str(name_matches[0]["id"])
    if len(name_matches) > 1:
        matches = [str(home.get("id")) for home in name_matches]
        raise ValueError(f"Home name '{home_ref}' is ambiguous: {matches}")

    available = [f"{home.get('name')} ({home.get('id')})" for home in homes]
    raise ValueError(f"Home '{home_ref}' not found. Available homes: {available}")


def resolve_controlled_openers_target(
    raw_homesdata: dict[str, Any], module_ref: str
) -> tuple[str, str, str]:
    """Resolve an NXS module and its home and bridge from raw homesdata."""
    body = raw_homesdata.get("body")
    homes = body.get("homes", []) if isinstance(body, dict) else []
    candidates = [
        (home, module)
        for home in homes
        if isinstance(home, dict)
        for module in home.get("modules") or []
        if isinstance(module, dict) and module.get("type") == "NXS"
    ]

    id_matches = [
        (home, module)
        for home, module in candidates
        if str(module.get("id")) == module_ref
    ]
    if len(id_matches) == 1:
        home, module = id_matches[0]
    else:
        name_matches = [
            (home, module)
            for home, module in candidates
            if str(module.get("name") or "").casefold() == module_ref.casefold()
        ]
        if len(name_matches) == 1:
            home, module = name_matches[0]
        elif len(name_matches) > 1:
            matches = [str(module.get("id")) for _, module in name_matches]
            raise ValueError(f"NXS module name '{module_ref}' is ambiguous: {matches}")
        else:
            available = [
                f"{home.get('name')} / {module.get('name')} ({module.get('id')})"
                for home, module in candidates
            ]
            raise ValueError(
                f"NXS module '{module_ref}' not found. Available modules: {available}"
            )

    home_id = str(home.get("id") or "")
    module_id = str(module.get("id") or "")
    bridge_id = str(module.get("bridge") or "")
    if not home_id or not module_id or not bridge_id:
        raise ValueError(f"NXS module '{module_ref}' is missing home or bridge data")
    return home_id, module_id, bridge_id


def resolve_module_bridge_id(
    account: pyatmo.AsyncAccount,
    home: pyatmo.Home,
    module: pyatmo.Module,
    gateway_ref: str | None,
) -> str:
    """Resolve the gateway bridge ID for a cover command."""

    if gateway_ref:
        gateway_home, gateway = find_gateway(account, gateway_ref)
        if gateway_home.entity_id != home.entity_id:
            raise ValueError(
                f"Gateway {gateway.entity_id} is in home {gateway_home.entity_id}, "
                f"but module {module.entity_id} is in home {home.entity_id}"
            )
        return gateway.entity_id

    nxg_ids = [
        module_id
        for module_id, candidate in home.modules.items()
        if candidate.device_type == DeviceType.NXG
    ]
    bridge_id = resolve_bridge_id(getattr(module, "bridge", None), nxg_ids)
    if bridge_id is not None:
        return bridge_id

    available = [
        f"{candidate.name} ({module_id})"
        for module_id, candidate in home.modules.items()
        if candidate.device_type == DeviceType.NXG
    ]
    raise ValueError(
        f"Could not resolve gateway for {module.entity_id}. "
        f"Provide --gateway. Available gateways: {available}"
    )


async def trigger_gateway_key_retrieval(
    auth: VeluxAsyncAuth,
    home: pyatmo.Home,
    gateway: pyatmo.Module,
) -> dict[str, Any]:
    """Ask the cloud to put the gateway in local key retrieval mode."""

    response = await auth.async_post_api_request(
        endpoint=SETSTATE_ENDPOINT,
        params={
            "json": {
                "home": {
                    "id": home.entity_id,
                    "modules": [{"id": gateway.entity_id, "retrieve_key": True}],
                }
            }
        },
    )
    try:
        raw = await response.json(content_type=None)
    except (aiohttp.ContentTypeError, ValueError) as err:
        raise RuntimeError("Unexpected retrieve_key response") from err
    message = retrieve_key_error(response.ok, response.status, raw)
    if message:
        raise RuntimeError(f"VELUX {message}")
    if not isinstance(raw, dict):
        return {"raw": raw}
    return raw


async def prompt_for_gateway_button(
    no_prompt: bool,
    home: pyatmo.Home,
    gateway: pyatmo.Module,
) -> None:
    """Pause until the operator has completed the physical gateway step."""

    if no_prompt:
        return

    instruction = (
        f"Cloud trigger sent for {gateway.name} ({gateway.entity_id}) in "
        f"{home.name}.\n"
        "Wait until the gateway light blinks green, briefly press the "
        "configuration button, then wait for slow white pulsing."
    )
    print(instruction, file=sys.stderr)
    await asyncio.to_thread(input, "Press Enter here once the light is pulsing white: ")


def require_interactive_gateway_prompt(no_prompt: bool) -> None:
    """Reject non-interactive runs unless the operator opts out of prompting."""

    if no_prompt or sys.stdin.isatty():
        return

    raise RuntimeError(
        "The gateway button step needs an interactive terminal. "
        "Use --no-prompt only if the gateway is already in slow-white Netcom mode.",
    )


def serialize_account(
    account: pyatmo.AsyncAccount,
    raw_status_by_home_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Serialize account data."""

    raw_status_by_home_id = raw_status_by_home_id or {}
    homes = [
        serialize_home(home, raw_status_by_home_id.get(home.entity_id))
        for home in sorted(account.homes.values(), key=home_key)
    ]
    return {"user": account.user, "homes": homes}


def serialize_home(
    home: pyatmo.Home,
    raw_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Serialize a home."""

    raw_status = raw_status or {}
    raw_home = raw_status_home(raw_status)
    raw_rooms = {
        room["id"]: room
        for room in raw_home.get("rooms") or []
        if isinstance(room, dict) and room.get("id")
    }
    raw_modules = {
        module["id"]: module
        for module in raw_home.get("modules") or []
        if isinstance(module, dict) and module.get("id")
    }
    rooms = [
        serialize_room(room, raw_rooms.get(room.entity_id))
        for room in sorted(
            home.rooms.values(), key=lambda item: (item.name, item.entity_id)
        )
    ]
    modules = []
    for module in sorted(home.modules.values(), key=module_key):
        serialized = serialize_module(home, module, raw_modules.get(module.entity_id))
        if module.device_type == DeviceType.NXG:
            serialized["reachable"] = gateway_reachable(raw_status, module.entity_id)
        modules.append(serialized)
    return {"id": home.entity_id, "name": home.name, "rooms": rooms, "modules": modules}


def serialize_room(
    room: pyatmo.Room,
    raw_room: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Serialize a room, including raw VELUX climate measurements if present."""

    data: dict[str, Any] = {
        "id": room.entity_id,
        "name": room.name,
        "device_types": sorted(device_type.value for device_type in room.device_types),
    }
    raw_room = raw_room or {}
    for key in ROOM_MEASUREMENT_KEYS:
        add_if_present(data, key, raw_room.get(key))
    return data


def serialize_module(
    home: pyatmo.Home,
    module: pyatmo.Module,
    raw_module: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Serialize a module."""

    room = home.rooms.get(module.room_id) if module.room_id else None
    data: dict[str, Any] = {
        "id": module.entity_id,
        "name": module.name,
        "label": module_label(home, module),
        "type": module.device_type.value,
        "category": module.device_category.value if module.device_category else None,
        "home_id": home.entity_id,
        "home_name": home.name,
        "room_id": module.room_id,
        "room_name": room.name if room else None,
        "bridge": module.bridge,
        "reachable": module.reachable,
    }

    raw_module = raw_module or {}
    raw_type = raw_module.get("type")
    if raw_type in BATTERY_MODULE_TYPES:
        add_if_present(data, "api_type", raw_type)
        for key in MODULE_STATUS_KEYS:
            if key == "type":
                continue
            add_if_present(data, key, raw_module.get(key))

    add_if_present(
        data, "features", sorted(module.features) if module.features else None
    )
    add_if_present(data, "bridged_modules", module.modules)
    add_if_present(data, "current_position", getattr(module, "current_position", None))
    add_if_present(data, "target_position", getattr(module, "target_position", None))
    add_if_present(data, "manufacturer", getattr(module, "manufacturer", None))
    add_if_present(data, "mode", getattr(module, "mode", None))
    add_if_present(data, "silent", getattr(module, "silent", None))
    add_if_present(data, "velux_type", getattr(module, "velux_type", None))
    add_if_present(data, "subtype", getattr(module, "subtype", None))
    add_if_present(data, "wifi_strength", getattr(module, "wifi_strength", None))
    add_if_present(data, "locked", getattr(module, "locked", None))
    add_if_present(data, "locking", getattr(module, "locking", None))
    add_if_present(data, "secure", getattr(module, "secure", None))
    add_if_present(data, "busy", getattr(module, "busy", None))
    add_if_present(data, "calibrating", getattr(module, "calibrating", None))
    add_if_present(data, "is_raining", getattr(module, "is_raining", None))
    add_if_present(data, "pairing", getattr(module, "pairing", None))
    add_if_present(data, "pincode_enabled", getattr(module, "pincode_enabled", None))
    add_if_present(data, "hardware_version", getattr(module, "hardware_version", None))
    add_if_present(
        data, "firmware_revision", getattr(module, "firmware_revision", None)
    )
    add_if_present(
        data,
        "firmware_revision_netatmo",
        getattr(module, "firmware_revision_netatmo", None),
    )
    add_if_present(
        data,
        "firmware_revision_thirdparty",
        getattr(module, "firmware_revision_thirdparty", None),
    )
    add_if_present(data, "last_seen", getattr(module, "last_seen", None))
    return data


def raw_status_home(raw_status: dict[str, Any]) -> dict[str, Any]:
    """Return the nested home payload from a raw homestatus response."""

    body = raw_status.get("body")
    if not isinstance(body, dict):
        return {}
    home = body.get("home")
    return home if isinstance(home, dict) else {}


def serialize_signing_key(
    key: SigningKey,
    gateway_id: str | None,
) -> dict[str, Any]:
    """Serialize local signing key material."""

    data = {
        "gateway_id": gateway_id,
        "sign_key_id": key.sign_key_id,
        "hash_sign_key": key.hash_sign_key,
    }
    add_if_present(data, "key_id", decode_urlsafe_b64(key.sign_key_id).hex())
    add_if_present(data, "key_value", decode_urlsafe_b64(key.hash_sign_key).hex())
    return data


def decode_urlsafe_b64(value: str) -> bytes:
    """Decode URL-safe Base64 text."""

    normalized = value.strip().replace("-", "+").replace("_", "/")
    normalized += "=" * (-len(normalized) % 4)
    return base64.b64decode(normalized, validate=True)


def add_if_present(data: dict[str, Any], key: str, value: Any) -> None:
    """Add a key when a value is present."""

    if value is not None:
        data[key] = value


def module_label(home: pyatmo.Home, module: pyatmo.Module) -> str:
    """Build a human-friendly module label."""

    room = home.rooms.get(module.room_id) if module.room_id else None

    if module.device_type.value == "NXO":
        parts = []
        if room is not None:
            parts.append(room.name)
        velux_type = getattr(module, "velux_type", None)
        if velux_type:
            parts.append(velux_type.replace("_", " "))
        if parts:
            return " - ".join(parts)

    return module.name


def home_key(home: pyatmo.Home) -> tuple[str, str]:
    """Sort key for homes."""

    return (home.name, home.entity_id)


def module_key(module: pyatmo.Module) -> tuple[str, str]:
    """Sort key for modules."""

    return (module.name, module.entity_id)


def find_cover(
    account: pyatmo.AsyncAccount, cover_ref: str
) -> tuple[pyatmo.Home, pyatmo.Module]:
    """Find a cover by entity ID or exact name."""

    covers = [
        (home, module)
        for home in account.homes.values()
        for module in home.modules.values()
        if is_cover_module(module)
    ]

    id_matches = [
        (home, module) for home, module in covers if module.entity_id == cover_ref
    ]
    if len(id_matches) == 1:
        return id_matches[0]

    name_matches = [
        (home, module)
        for home, module in covers
        if module.name.casefold() == cover_ref.casefold()
    ]
    if len(name_matches) == 1:
        return name_matches[0]
    if len(name_matches) > 1:
        matches = [module.entity_id for _, module in name_matches]
        raise ValueError(f"Cover name '{cover_ref}' is ambiguous: {matches}")

    label_matches = [
        (home, module)
        for home, module in covers
        if module_label(home, module).casefold() == cover_ref.casefold()
    ]
    if len(label_matches) == 1:
        return label_matches[0]
    if len(label_matches) > 1:
        matches = [
            f"{module_label(home, module)} ({module.entity_id})"
            for home, module in label_matches
        ]
        raise ValueError(f"Cover label '{cover_ref}' is ambiguous: {matches}")

    available = [
        f"{module_label(home, module)} ({module.entity_id})" for home, module in covers
    ]
    raise ValueError(f"Cover '{cover_ref}' not found. Available covers: {available}")


def is_cover_module(module: pyatmo.Module) -> bool:
    """Return True for modules that support VELUX position control."""

    return hasattr(module, "current_position") and hasattr(
        module, "async_set_target_position"
    )


def find_gateway(
    account: pyatmo.AsyncAccount,
    gateway_ref: str | None,
) -> tuple[pyatmo.Home, pyatmo.Module]:
    """Find a VELUX gateway by entity ID, exact name, or unique account gateway."""

    gateways = [
        (home, module)
        for home in account.homes.values()
        for module in home.modules.values()
        if module.device_type == DeviceType.NXG
    ]
    if not gateways:
        raise ValueError("No VELUX NXG gateway found")

    if gateway_ref is None:
        if len(gateways) == 1:
            return gateways[0]
        available = [
            f"{home.name} / {module.name} ({module.entity_id})"
            for home, module in gateways
        ]
        raise ValueError(f"Multiple gateways found. Provide --gateway: {available}")

    id_matches = [
        (home, module) for home, module in gateways if module.entity_id == gateway_ref
    ]
    if len(id_matches) == 1:
        return id_matches[0]

    name_matches = [
        (home, module)
        for home, module in gateways
        if module.name.casefold() == gateway_ref.casefold()
    ]
    if len(name_matches) == 1:
        return name_matches[0]
    if len(name_matches) > 1:
        matches = [module.entity_id for _, module in name_matches]
        raise ValueError(f"Gateway name '{gateway_ref}' is ambiguous: {matches}")

    available = [
        f"{home.name} / {module.name} ({module.entity_id})" for home, module in gateways
    ]
    raise ValueError(
        f"Gateway '{gateway_ref}' not found. Available gateways: {available}"
    )


def print_json(payload: dict[str, Any], *, stream: Any = sys.stdout) -> None:
    """Print JSON output."""

    json.dump(payload, stream, indent=2)
    stream.write("\n")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    """Run the requested command."""

    if args.command == "login":
        return await command_login(args)
    if args.command == "list-devices":
        return await command_list_devices(args)
    if args.command == "raw-homesdata":
        return await command_raw_homesdata(args)
    if args.command == "raw-homestatus":
        return await command_raw_homestatus(args)
    if args.command == "get-configs":
        return await command_get_configs(args)
    if args.command == "set-controlled-openers":
        return await command_set_controlled_openers(args)
    if args.command == "set-cover-position":
        return await command_set_cover_position(args)
    if args.command == "stop-cover":
        return await command_stop_cover(args)
    if args.command == "stop-gateway":
        return await command_stop_gateway(args)
    if args.command == "set-window-mode":
        return await command_set_window_mode(args)
    if args.command == "retrieve-key":
        return await command_retrieve_key(args)

    raise ValueError(f"Unsupported command: {args.command}")


def main() -> int:
    """Run the CLI."""

    parser = build_parser()
    args = parser.parse_args()

    try:
        result = asyncio.run(run(args))
    except KeyboardInterrupt:
        print_json(
            {
                "ok": False,
                "error": {"type": "KeyboardInterrupt", "message": "Interrupted"},
            },
            stream=sys.stderr,
        )
        return 130
    except Exception as err:
        print_json(
            {
                "ok": False,
                "error": {"type": type(err).__name__, "message": str(err)},
            },
            stream=sys.stderr,
        )
        return 1

    print_json({"ok": True, "result": result})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
