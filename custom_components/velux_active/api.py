"""API client for Velux Active with Netatmo."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from json import JSONDecodeError
import time
from typing import Any, Callable

import aiohttp
from pyatmo.account import AsyncAccount
from pyatmo.auth import AbstractAsyncAuth
from pyatmo.const import (
    AUTH_REQ_ENDPOINT,
    GETHOMESDATA_ENDPOINT,
    GETHOMESTATUS_ENDPOINT,
    HOME,
    SETSTATE_ENDPOINT,
)
from pyatmo.enums import ScheduleType
from pyatmo.exceptions import NoDeviceError
from pyatmo.helpers import extract_raw_data
from pyatmo.home import Home
from pyatmo.modules import NXO

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_AT,
    LOGGER,
)
from .signing import retrieve_key_error

# Work around pyatmo 9.4.0 until https://github.com/jabesq-org/pyatmo/pull/564 is released.
ScheduleType._value2member_map_.setdefault("algo", ScheduleType.AUTO)

DEFAULT_CLIENT_ID = "5931426da127d981e76bdd3f"
DEFAULT_CLIENT_SECRET = "6ae2d89d15e767ae5c56b456b452d319"
DEFAULT_APP_VERSION = "791302006"
DEFAULT_SCOPE = "velux_scopes"
DEFAULT_TIMEOUT = 10.0
DEFAULT_USER_PREFIX = "velux"


class VeluxActiveError(Exception):
    """Base exception for the integration."""


class VeluxActiveCannotConnect(VeluxActiveError):
    """Raised when the API cannot be reached."""


class VeluxActiveInvalidAuth(VeluxActiveError):
    """Raised when credentials are invalid."""


@dataclass(slots=True)
class OAuthTokens:
    access_token: str
    refresh_token: str | None
    expires_at: int | None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> OAuthTokens | None:
        access_token = str(data.get(CONF_ACCESS_TOKEN) or "")
        refresh_token = data.get(CONF_REFRESH_TOKEN)
        expires_at = data.get(CONF_TOKEN_EXPIRES_AT)

        if not access_token and not refresh_token:
            return None

        return cls(
            access_token=access_token,
            refresh_token=str(refresh_token) if refresh_token else None,
            expires_at=int(expires_at) if expires_at is not None else None,
        )

    def as_storage_dict(self) -> dict[str, Any]:
        return {
            CONF_ACCESS_TOKEN: self.access_token,
            CONF_REFRESH_TOKEN: self.refresh_token,
            CONF_TOKEN_EXPIRES_AT: self.expires_at,
        }


@dataclass(slots=True)
class VeluxActiveData:
    user: str | None
    homes: dict[str, Home]
    covers: dict[str, Any]
    rooms: dict[str, dict[str, Any]]
    sensor_modules: dict[str, dict[str, Any]]


BATTERY_MODULE_TYPES = frozenset({"NXS", "NXD"})
ROOM_MEASUREMENT_KEYS = frozenset(
    {"temperature", "co2", "humidity", "lux", "air_quality"}
)


class VeluxActiveAuth(AbstractAsyncAuth):
    def __init__(
        self,
        websession: aiohttp.ClientSession,
        *,
        username: str,
        password: str,
        initial_tokens: OAuthTokens | None = None,
        token_updated: Callable[[OAuthTokens], None] | None = None,
    ) -> None:
        super().__init__(websession)
        self._username = username
        self._password = password
        self._token_updated = token_updated
        self._tokens: OAuthTokens | None = initial_tokens

    async def async_get_access_token(self) -> str:
        if self._tokens and self._tokens.access_token and self._is_token_valid(self._tokens):
            return self._tokens.access_token

        if self._tokens and self._tokens.refresh_token:
            try:
                await self.async_refresh()
            except VeluxActiveInvalidAuth:
                await self.async_login()
        else:
            await self.async_login()

        if self._tokens is None:
            raise VeluxActiveInvalidAuth("No access token available")

        return self._tokens.access_token

    async def async_login(self) -> OAuthTokens:
        return await self._async_request_tokens(
            {
                "grant_type": "password",
                "username": self._username,
                "password": self._password,
                "scope": DEFAULT_SCOPE,
                "user_prefix": DEFAULT_USER_PREFIX,
            }
        )

    async def async_refresh(self) -> OAuthTokens:
        if self._tokens is None or not self._tokens.refresh_token:
            raise VeluxActiveInvalidAuth("Refresh token is not available")

        return await self._async_request_tokens(
            {
                "grant_type": "refresh_token",
                "refresh_token": self._tokens.refresh_token,
            }
        )

    async def process_response(
        self,
        response: aiohttp.ClientResponse,
        url: str,
    ) -> aiohttp.ClientResponse:
        """Process API responses and log setstate body errors."""
        response = await super().process_response(response, url)
        if not url.endswith(SETSTATE_ENDPOINT):
            return response

        try:
            raw: Any = await response.json(content_type=None)
        except (aiohttp.ContentTypeError, JSONDecodeError):
            return response

        body = raw.get("body") if isinstance(raw, dict) else None
        errors = body.get("errors") if isinstance(body, dict) else None
        if errors:
            LOGGER.warning(
                "VELUX Active setstate response returned API errors: "
                "api_errors=%s api_response=%s",
                errors,
                raw,
            )

        return response

    def _is_token_valid(self, tokens: OAuthTokens) -> bool:
        return tokens.expires_at is None or int(time.time()) < (tokens.expires_at - 60)

    @property
    def tokens(self) -> OAuthTokens | None:
        return self._tokens

    async def _async_request_tokens(self, payload: dict[str, str]) -> OAuthTokens:
        url = f"{self.base_url}{AUTH_REQ_ENDPOINT}"
        data = {
            "client_id": DEFAULT_CLIENT_ID,
            "client_secret": DEFAULT_CLIENT_SECRET,
            "app_version": DEFAULT_APP_VERSION,
            **payload,
        }

        try:
            async with self.websession.post(
                url,
                data=data,
                timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT),
            ) as response:
                text = await response.text()
                if not text.strip():
                    raise VeluxActiveCannotConnect(
                        f"Empty response from auth endpoint (status {response.status})"
                    )
                try:
                    raw: Any = json.loads(text)
                except Exception:
                    raw = {"raw": text}
        except (aiohttp.ClientError, TimeoutError) as err:
            raise VeluxActiveCannotConnect(str(err)) from err

        if not response.ok:
            self._raise_for_auth_response(response.status, raw)

        if not isinstance(raw, dict) or "access_token" not in raw:
            raise VeluxActiveCannotConnect("Unexpected token response")

        issued_at = int(time.time())
        expires_in = raw.get("expires_in", raw.get("expire_in"))
        expires_at = issued_at + int(expires_in) if expires_in is not None else None

        tokens = OAuthTokens(
            access_token=str(raw["access_token"]),
            refresh_token=str(raw["refresh_token"]) if raw.get("refresh_token") else None,
            expires_at=expires_at,
        )

        self._tokens = tokens
        if self._token_updated:
            self._token_updated(tokens)

        return tokens

    def _raise_for_auth_response(self, status: int, raw: Any) -> None:
        error = ""
        if isinstance(raw, dict):
            error = str(raw.get("error") or raw.get("message") or "")

        if status in {400, 401} or error == "invalid_grant":
            raise VeluxActiveInvalidAuth(error or "Invalid credentials")

        raise VeluxActiveCannotConnect(error or f"Authentication failed with {status}")


def _is_cover_module(module: Any) -> bool:
    """Return True for any module that supports position control.

    NXO covers both roller shutters and roof windows in the Velux API.
    We also duck-type check so that if pyatmo introduces a separate window
    class it is picked up automatically without a code change.
    """
    if isinstance(module, NXO):
        return True
    return (
        hasattr(module, "current_position")
        and hasattr(module, "async_set_target_position")
    )


class VeluxActiveClient:
    def __init__(
        self,
        websession: aiohttp.ClientSession,
        username: str,
        password: str,
        *,
        initial_tokens: OAuthTokens | None = None,
        token_updated: Callable[[OAuthTokens], None] | None = None,
    ) -> None:
        self._auth = VeluxActiveAuth(
            websession,
            username=username,
            password=password,
            initial_tokens=initial_tokens,
            token_updated=token_updated,
        )
        self._account = AsyncAccount(self._auth)
        self._username = username

    async def async_validate(self) -> str:
        await self.async_setup()  # Load topology first
        data = await self.async_update()
        home_names = [home.name for home in data.homes.values()]
        return home_names[0] if len(home_names) == 1 else self._username

    async def async_get_raw_homesdata(self) -> dict[str, Any]:
        """Return raw homesdata from the Velux API."""
        response = await self._auth.async_post_api_request(endpoint=GETHOMESDATA_ENDPOINT)
        return await response.json()

    async def async_get_raw_homestatus(self, home_id: str) -> dict[str, Any]:
        """Return raw homestatus from the Velux API."""
        response = await self._auth.async_post_api_request(
            endpoint=GETHOMESTATUS_ENDPOINT,
            params={"home_id": home_id},
        )
        return await response.json()

    async def async_trigger_retrieve_key(self, home_id: str, gateway_id: str) -> None:
        """Ask the gateway to open its temporary local key retrieval listener."""
        response = await self._auth.async_post_api_request(
            endpoint=SETSTATE_ENDPOINT,
            params={
                "json": {
                    "home": {
                        "id": home_id,
                        "modules": [{"id": gateway_id, "retrieve_key": True}],
                    }
                }
            },
        )
        try:
            raw: Any = await response.json(content_type=None)
        except (aiohttp.ContentTypeError, JSONDecodeError) as err:
            raise VeluxActiveCannotConnect("Unexpected retrieve_key response") from err
        message = retrieve_key_error(response.ok, response.status, raw)
        if message:
            raise VeluxActiveCannotConnect(f"VELUX {message}")

    async def async_reauthenticate(self) -> None:
        """Force a fresh password login and store the new tokens."""
        await self._auth.async_login()

    async def async_setup(self) -> None:
        """Fetch topology once at startup. Called once — homesdata is expensive."""
        await self._account.async_update_topology()

    async def async_update(self) -> VeluxActiveData:
        """Fetch current device status only. Topology is loaded once at startup."""
        LOGGER.debug(
            "VELUX Active topology found %d homes: home_ids=%s",
            len(self._account.homes),
            sorted(self._account.homes),
        )
        raw_status_by_home_id: dict[str, dict[str, Any]] = {}
        for home_id, home in list(self._account.homes.items()):
            if not home.modules:
                LOGGER.debug(
                    "Skipping VELUX Active home without modules: home_id=%s name=%s",
                    home_id,
                    home.name,
                )
                self._account.homes.pop(home_id, None)
                continue

            LOGGER.debug(
                "Requesting VELUX Active home status: home_id=%s name=%s modules=%d",
                home_id,
                home.name,
                len(home.modules),
            )
            try:
                raw_status = await self.async_get_raw_homestatus(home_id)
                raw_data = extract_raw_data(raw_status, HOME)
                await home.update(raw_data, do_raise_for_reachability_error=True)
                raw_status_by_home_id[home_id] = raw_status
            except NoDeviceError as err:
                LOGGER.warning(
                    "Keeping VELUX Active home with topology-only data because "
                    "status data is unavailable: "
                    "home_id=%s name=%s modules=%d error=%s",
                    home_id,
                    home.name,
                    len(home.modules),
                    err,
                )

        covers = {
            module_id: module
            for home in self._account.homes.values()
            for module_id, module in home.modules.items()
            if _is_cover_module(module)
        }

        LOGGER.debug(
            "Cover modules found: %s",
            {mid: type(m).__name__ for mid, m in covers.items()},
        )

        rooms, sensor_modules = _extract_climate_status(
            self._account.homes, raw_status_by_home_id
        )

        return VeluxActiveData(
            user=self._account.user,
            homes=dict(self._account.homes),
            covers=covers,
            rooms=rooms,
            sensor_modules=sensor_modules,
        )

    @property
    def tokens(self) -> OAuthTokens | None:
        return self._auth.tokens


def _extract_climate_status(
    homes: Mapping[str, Home],
    raw_status_by_home_id: Mapping[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Extract VELUX room measurements and battery modules from raw homestatus."""
    rooms: dict[str, dict[str, Any]] = {}
    sensor_modules: dict[str, dict[str, Any]] = {}

    for home_id, raw_status in raw_status_by_home_id.items():
        home = homes.get(home_id)
        raw_home = _raw_status_home(raw_status)

        for raw_room in raw_home.get("rooms") or []:
            if not isinstance(raw_room, Mapping) or not _has_room_measurement(raw_room):
                continue

            room_id = str(raw_room.get("id") or "")
            if not room_id:
                continue

            room = home.rooms.get(room_id) if home is not None else None
            room_data = dict(raw_room)
            room_data["home_id"] = home_id
            if room is not None:
                room_data["name"] = room.name
                room_data["module_ids"] = list(room.modules)
            rooms[_status_key(home_id, room_id)] = room_data

        for raw_module in raw_home.get("modules") or []:
            if not isinstance(raw_module, Mapping):
                continue
            module_type = raw_module.get("type")
            if module_type not in BATTERY_MODULE_TYPES:
                continue

            module_id = str(raw_module.get("id") or "")
            if not module_id:
                continue

            module = home.modules.get(module_id) if home is not None else None
            module_data = dict(raw_module)
            module_data["home_id"] = home_id
            if module is not None:
                module_data["name"] = module.name
                module_data["room_id"] = module.room_id
                room = home.rooms.get(module.room_id) if module.room_id else None
                if room is not None:
                    module_data["room_name"] = room.name
            sensor_modules[module_id] = module_data

    return rooms, sensor_modules


def _raw_status_home(raw_status: Mapping[str, Any]) -> dict[str, Any]:
    """Return the nested home payload from a raw homestatus response."""
    body = raw_status.get("body")
    if not isinstance(body, Mapping):
        return {}
    home = body.get("home")
    return dict(home) if isinstance(home, Mapping) else {}


def _has_room_measurement(raw_room: Mapping[str, Any]) -> bool:
    """Return True when a raw room carries VELUX climate measurements."""
    return any(key in raw_room for key in ROOM_MEASUREMENT_KEYS)


def _status_key(home_id: str, entity_id: str) -> str:
    """Scope homestatus room IDs by home to avoid cross-home collisions."""
    return f"{home_id}:{entity_id}"
