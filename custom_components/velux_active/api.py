"""API client for Velux Active with Netatmo."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Any

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
from pyatmo.exceptions import ApiHomeReachabilityError, NoDeviceError
from pyatmo.helpers import extract_raw_data
from pyatmo.home import Home
from pyatmo.modules import NXO

from .connectivity import gateway_reachable
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_AT,
    CONTROLLED_OPENERS_OPTIONS,
    GETCONFIGS_ENDPOINT,
    LOGGER,
    SETCONFIGS_ENDPOINT,
    VELUX_API_URL,
    VELUX_APP_VERSION,
)
from .realtime import async_iter_events
from .signing import retrieve_key_error

# Work around pyatmo 9.4.0 until https://github.com/jabesq-org/pyatmo/pull/564 is released.
ScheduleType._value2member_map_.setdefault("algo", ScheduleType.AUTO)

DEFAULT_CLIENT_ID = "5931426da127d981e76bdd3f"
DEFAULT_CLIENT_SECRET = "6ae2d89d15e767ae5c56b456b452d319"
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
    gateway_connectivity: dict[str, bool | None]
    gateway_status: dict[str, dict[str, Any]]
    rooms: dict[str, dict[str, Any]]
    sensor_modules: dict[str, dict[str, Any]]
    controlled_openers: dict[str, dict[str, str]]


BATTERY_MODULE_TYPES = frozenset({"NXS", "NXD"})
ROOM_MEASUREMENT_KEYS = frozenset(
    {"temperature", "co2", "humidity", "lux", "air_quality"}
)
REALTIME_COVER_FIELDS = (
    "current_position",
    "target_position",
    "reachable",
    "mode",
    "last_seen",
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
        if (
            self._tokens
            and self._tokens.access_token
            and self._is_token_valid(self._tokens)
        ):
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
            "app_version": VELUX_APP_VERSION,
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
            refresh_token=str(raw["refresh_token"])
            if raw.get("refresh_token")
            else None,
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
    return hasattr(module, "current_position") and hasattr(
        module, "async_set_target_position"
    )


def _optional_int(value: Any) -> int | None:
    """Return an integer value when possible."""
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


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
        self._controlled_openers_by_home: dict[str, dict[str, dict[str, str]]] = {}

    async def async_validate(self) -> None:
        """Validate credentials by loading account topology and status."""
        await self.async_setup()  # Load topology first
        await self.async_update()

    def async_realtime_events(self) -> AsyncIterator[dict[str, Any]]:
        """Yield embedded events from the VELUX app WebSocket."""
        return async_iter_events(
            self._auth.websession,
            self._auth.async_get_access_token,
            VELUX_APP_VERSION,
        )

    def apply_realtime_cover_event(self, event: Mapping[str, Any]) -> bool:
        """Apply one partial WebSocket event to known cover objects."""
        raw_home = event.get("home")
        if not isinstance(raw_home, Mapping):
            return False

        home = self._account.homes.get(str(raw_home.get("id") or ""))
        if home is None:
            return False

        raw_modules = raw_home.get("modules")
        if not isinstance(raw_modules, list):
            return False

        event_timestamp = _optional_int(event.get("timestamp"))
        changed = False
        for raw_module in raw_modules:
            if not isinstance(raw_module, Mapping):
                continue

            module = home.modules.get(str(raw_module.get("id") or ""))
            if module is None or not _is_cover_module(module):
                continue

            last_seen = _optional_int(raw_module.get("last_seen"))
            incoming_timestamp = last_seen if last_seen is not None else event_timestamp
            current_timestamp = _optional_int(getattr(module, "last_seen", None))
            if (
                incoming_timestamp is not None
                and current_timestamp is not None
                and incoming_timestamp < current_timestamp
            ):
                continue

            for field in REALTIME_COVER_FIELDS:
                if field not in raw_module:
                    continue
                value = raw_module[field]
                if getattr(module, field, None) != value:
                    setattr(module, field, value)
                    changed = True

        return changed

    async def async_get_raw_homesdata(self) -> dict[str, Any]:
        """Return raw homesdata from the Velux API."""
        response = await self._auth.async_post_api_request(
            endpoint=GETHOMESDATA_ENDPOINT
        )
        return await response.json()

    async def async_get_raw_homestatus(self, home_id: str) -> dict[str, Any]:
        """Return raw homestatus from the Velux API."""
        response = await self._auth.async_post_api_request(
            endpoint=GETHOMESTATUS_ENDPOINT,
            params={"home_id": home_id},
        )
        return await response.json()

    async def async_get_configs(self, home_id: str) -> dict[str, Any]:
        """Return the VELUX app configuration for one home."""
        return await self._async_sync_api_request(
            "GET",
            GETCONFIGS_ENDPOINT,
            params={"home_id": home_id},
        )

    async def async_set_controlled_openers(
        self,
        home_id: str,
        module_id: str,
        bridge_id: str,
        controlled_openers: str,
    ) -> None:
        """Set which opening type an indoor climate sensor controls."""
        if controlled_openers not in CONTROLLED_OPENERS_OPTIONS:
            raise ValueError(f"Unsupported controlled_openers: {controlled_openers}")

        await self._async_sync_api_request(
            "POST",
            SETCONFIGS_ENDPOINT,
            json_data={
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
        self._controlled_openers_by_home.setdefault(home_id, {})[module_id] = {
            "home_id": home_id,
            "bridge": bridge_id,
            "controlled_openers": controlled_openers,
        }

    async def _async_sync_api_request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, str] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send an authenticated request to the VELUX sync API."""
        access_token = await self._auth.async_get_access_token()
        headers = {"Authorization": f"Bearer {access_token}"}
        request_kwargs: dict[str, Any] = {
            "headers": headers,
            "timeout": aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT),
        }
        if params is not None:
            request_kwargs["params"] = params
        if json_data is not None:
            headers["Content-Type"] = "application/json"
            request_kwargs["json"] = json_data

        try:
            async with self._auth.websession.request(
                method,
                f"{VELUX_API_URL}{endpoint}",
                **request_kwargs,
            ) as response:
                text = await response.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise VeluxActiveCannotConnect(str(err)) from err

        if not text.strip():
            raise VeluxActiveCannotConnect(
                f"{endpoint} returned empty response (status {response.status})"
            )
        try:
            raw: Any = json.loads(text)
        except JSONDecodeError as err:
            raise VeluxActiveCannotConnect(
                f"{endpoint} returned an invalid JSON response"
            ) from err

        body = raw.get("body") if isinstance(raw, dict) else None
        errors = body.get("errors") if isinstance(body, dict) else None
        if (
            not response.ok
            or not isinstance(raw, dict)
            or raw.get("status") != "ok"
            or errors
        ):
            raise VeluxActiveCannotConnect(
                f"{endpoint} failed with status {response.status}: {raw}"
            )
        return raw

    async def async_trigger_retrieve_key(self, home_id: str, gateway_id: str) -> None:
        """Ask the gateway to open its temporary local key retrieval listener."""
        LOGGER.debug("Requesting VELUX gateway key retrieval mode")
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
        LOGGER.debug("VELUX gateway accepted key retrieval request")

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
            raw_status: dict[str, Any] | None = None
            try:
                raw_status = await self.async_get_raw_homestatus(home_id)
                raw_status_by_home_id[home_id] = raw_status
                raw_data = extract_raw_data(raw_status, HOME)
                await home.update(raw_data, do_raise_for_reachability_error=True)
            except (ApiHomeReachabilityError, NoDeviceError) as err:
                if isinstance(err, ApiHomeReachabilityError):
                    connectivity = (
                        _extract_gateway_connectivity(
                            {home_id: home}, {home_id: raw_status}
                        )
                        if raw_status is not None
                        else {}
                    )
                    if False not in connectivity.values():
                        raise
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
        nxs_home_ids = {
            str(module["home_id"])
            for module in sensor_modules.values()
            if module.get("type") == "NXS" and module.get("home_id")
        }
        for home_id in sorted(nxs_home_ids):
            try:
                raw_configs = await self.async_get_configs(home_id)
            except VeluxActiveCannotConnect as err:
                LOGGER.debug(
                    "Keeping previous VELUX controlled opener configuration: "
                    "home_id=%s error=%s",
                    home_id,
                    err,
                )
            else:
                self._controlled_openers_by_home[home_id] = _extract_controlled_openers(
                    home_id, raw_configs
                )

        controlled_openers = {
            module_id: dict(config)
            for home_configs in self._controlled_openers_by_home.values()
            for module_id, config in home_configs.items()
            if module_id in sensor_modules
            and sensor_modules[module_id].get("type") == "NXS"
        }
        gateway_connectivity = _extract_gateway_connectivity(
            self._account.homes, raw_status_by_home_id
        )
        gateway_status = _extract_gateway_status(
            self._account.homes, raw_status_by_home_id
        )

        return VeluxActiveData(
            user=self._account.user,
            homes=dict(self._account.homes),
            covers=covers,
            gateway_connectivity=gateway_connectivity,
            gateway_status=gateway_status,
            rooms=rooms,
            sensor_modules=sensor_modules,
            controlled_openers=controlled_openers,
        )

    @property
    def tokens(self) -> OAuthTokens | None:
        return self._auth.tokens


def _extract_gateway_connectivity(
    homes: Mapping[str, Home],
    raw_status_by_home_id: Mapping[str, dict[str, Any]],
) -> dict[str, bool | None]:
    """Extract per-gateway connectivity from raw homestatus responses."""
    connectivity: dict[str, bool | None] = {}

    for home_id, home in homes.items():
        raw_status = raw_status_by_home_id.get(home_id, {})
        for gateway_id, module in home.modules.items():
            if type(module).__name__ != "NXG":
                continue
            connectivity[gateway_id] = gateway_reachable(raw_status, gateway_id)

    return connectivity


def _extract_gateway_status(
    homes: Mapping[str, Home],
    raw_status_by_home_id: Mapping[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Extract raw status fields for each known VELUX gateway."""
    gateway_status: dict[str, dict[str, Any]] = {}

    for home_id, home in homes.items():
        gateway_ids = {
            module_id
            for module_id, module in home.modules.items()
            if type(module).__name__ == "NXG"
        }
        raw_home = _raw_status_home(raw_status_by_home_id.get(home_id, {}))
        for raw_module in raw_home.get("modules") or []:
            if not isinstance(raw_module, Mapping):
                continue
            gateway_id = str(raw_module.get("id") or "")
            if gateway_id not in gateway_ids:
                continue
            gateway_status[gateway_id] = {**raw_module, "home_id": home_id}

    return gateway_status


def _extract_climate_status(
    homes: Mapping[str, Home],
    raw_status_by_home_id: Mapping[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Extract VELUX room measurements and battery modules from raw homestatus."""
    # TODO: Replace this raw-payload compatibility parser with pyatmo Room and
    # NXS/NXD data once Home Assistant ships a pyatmo release containing
    # https://github.com/jabesq-org/pyatmo/pull/634.
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


def _extract_controlled_openers(
    home_id: str, raw_configs: Mapping[str, Any]
) -> dict[str, dict[str, str]]:
    """Extract per-NXS controlled opener settings from getconfigs."""
    configs: dict[str, dict[str, str]] = {}
    raw_home = _raw_status_home(raw_configs)
    for raw_module in raw_home.get("modules") or []:
        if not isinstance(raw_module, Mapping):
            continue
        module_id = str(raw_module.get("id") or "")
        bridge_id = str(raw_module.get("bridge") or "")
        controlled_openers = raw_module.get("controlled_openers")
        if not module_id or not bridge_id or not isinstance(controlled_openers, str):
            continue
        configs[module_id] = {
            "home_id": home_id,
            "bridge": bridge_id,
            "controlled_openers": controlled_openers,
        }
    return configs


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
