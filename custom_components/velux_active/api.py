"""API client for Velux Active with Netatmo."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import time
from typing import Any, Callable
import logging

import aiohttp
from pyatmo.account import AsyncAccount
from pyatmo.auth import AbstractAsyncAuth
from pyatmo.const import AUTH_REQ_ENDPOINT
from pyatmo.home import Home
from pyatmo.modules import NXO

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_AT,
)

_LOGGER = logging.getLogger(__name__)

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
                try:
                    raw: Any = await response.json(content_type=None)
                except aiohttp.ContentTypeError:
                    raw = {"raw": await response.text()}
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
        data = await self.async_update()
        home_names = [home.name for home in data.homes.values()]
        return home_names[0] if len(home_names) == 1 else self._username

    async def async_setup(self) -> None:
        """Fetch topology once at startup. Called once — homesdata is expensive."""
        await self._account.async_update_topology()

    async def async_update(self) -> VeluxActiveData:
        """Fetch current device status only. Topology is loaded once at startup."""
        for home_id in list(self._account.homes):
            try:
                await self._account.async_update_status(home_id)
            except Exception as err:
                _LOGGER.debug("Skipping home %s: %s", home_id, err)

        covers = {
            module_id: module
            for home in self._account.homes.values()
            for module_id, module in home.modules.items()
            if _is_cover_module(module)
        }

        _LOGGER.debug(
            "Cover modules found: %s",
            {mid: type(m).__name__ for mid, m in covers.items()},
        )

        return VeluxActiveData(
            user=self._account.user,
            homes=dict(self._account.homes),
            covers=covers,
        )

    @property
    def tokens(self) -> OAuthTokens | None:
        return self._auth.tokens
