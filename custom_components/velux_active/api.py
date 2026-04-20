"""API client for Velux Active with Netatmo."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import time
from typing import Any, Callable

import aiohttp
from pyatmo.account import AsyncAccount
from pyatmo.auth import AbstractAsyncAuth
from pyatmo.const import AUTH_REQ_ENDPOINT, DEFAULT_BASE_URL
from pyatmo.home import Home
from pyatmo.modules import NXO

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_AT,
    CONF_TOKEN_ISSUED_AT,
)

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
    """Container for OAuth token data."""

    access_token: str
    refresh_token: str | None
    expires_at: int | None
    issued_at: int

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> OAuthTokens | None:
        """Build tokens from stored config entry data."""
        access_token = str(data.get(CONF_ACCESS_TOKEN) or "")
        refresh_token = data.get(CONF_REFRESH_TOKEN)
        expires_at = data.get(CONF_TOKEN_EXPIRES_AT)
        issued_at = data.get(CONF_TOKEN_ISSUED_AT)

        if not access_token and not refresh_token:
            return None

        return cls(
            access_token=access_token,
            refresh_token=str(refresh_token) if refresh_token else None,
            expires_at=int(expires_at) if expires_at is not None else None,
            issued_at=int(issued_at) if issued_at is not None else int(time.time()),
        )

    def as_storage_dict(self) -> dict[str, Any]:
        """Return a serializable token payload for config entry storage."""
        return {
            CONF_ACCESS_TOKEN: self.access_token,
            CONF_REFRESH_TOKEN: self.refresh_token,
            CONF_TOKEN_EXPIRES_AT: self.expires_at,
            CONF_TOKEN_ISSUED_AT: self.issued_at,
        }


@dataclass(slots=True)
class VeluxActiveAccountInfo:
    """Account information used during config flow."""

    title: str
    username: str
    home_ids: list[str]
    home_names: list[str]


@dataclass(slots=True)
class VeluxActiveData:
    """Current snapshot of the Velux account."""

    user: str | None
    homes: dict[str, Home]
    covers: dict[str, NXO]


class VeluxActiveAuth(AbstractAsyncAuth):
    """pyatmo auth adapter using the VELUX password grant."""

    def __init__(
        self,
        websession: aiohttp.ClientSession,
        *,
        username: str,
        password: str,
        initial_tokens: OAuthTokens | None = None,
        token_updated: Callable[[OAuthTokens], None] | None = None,
        base_url: str = DEFAULT_BASE_URL,
        client_id: str = DEFAULT_CLIENT_ID,
        client_secret: str = DEFAULT_CLIENT_SECRET,
        app_version: str = DEFAULT_APP_VERSION,
        scope: str = DEFAULT_SCOPE,
        timeout: float = DEFAULT_TIMEOUT,
        user_prefix: str = DEFAULT_USER_PREFIX,
    ) -> None:
        """Initialize the auth adapter."""
        super().__init__(websession, base_url=base_url)
        self._username = username
        self._password = password
        self._client_id = client_id
        self._client_secret = client_secret
        self._app_version = app_version
        self._scope = scope
        self._timeout = timeout
        self._token_updated = token_updated
        self._tokens: OAuthTokens | None = initial_tokens
        self._user_prefix = user_prefix

    async def async_get_access_token(self) -> str:
        """Return a valid access token for pyatmo requests."""
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
            msg = "No access token available"
            raise VeluxActiveInvalidAuth(msg)
        return self._tokens.access_token

    async def async_login(self) -> OAuthTokens:
        """Authenticate using email and password."""
        return await self._async_request_tokens(
            {
                "grant_type": "password",
                "username": self._username,
                "password": self._password,
                "scope": self._scope,
                "user_prefix": self._user_prefix,
            }
        )

    async def async_refresh(self) -> OAuthTokens:
        """Refresh the current access token."""
        if self._tokens is None or not self._tokens.refresh_token:
            msg = "Refresh token is not available"
            raise VeluxActiveInvalidAuth(msg)

        return await self._async_request_tokens(
            {
                "grant_type": "refresh_token",
                "refresh_token": self._tokens.refresh_token,
            }
        )

    def _is_token_valid(self, tokens: OAuthTokens) -> bool:
        """Return whether the current token is still valid."""
        return tokens.expires_at is None or int(time.time()) < (tokens.expires_at - 60)

    @property
    def tokens(self) -> OAuthTokens | None:
        """Return the latest OAuth token set."""
        return self._tokens

    async def _async_request_tokens(self, payload: dict[str, str]) -> OAuthTokens:
        """Request OAuth tokens."""
        url = f"{self.base_url}{AUTH_REQ_ENDPOINT}"
        data = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "app_version": self._app_version,
            **payload,
        }

        try:
            async with self.websession.post(
                url,
                data=data,
                timeout=aiohttp.ClientTimeout(total=self._timeout),
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
            msg = f"Unexpected token response from {url}"
            raise VeluxActiveCannotConnect(msg)

        issued_at = int(time.time())
        expires_in = raw.get("expires_in", raw.get("expire_in"))
        expires_at = (
            issued_at + int(expires_in) if expires_in is not None else None
        )
        new_tokens = OAuthTokens(
            access_token=str(raw["access_token"]),
            refresh_token=(
                str(raw["refresh_token"]) if raw.get("refresh_token") else None
            ),
            expires_at=expires_at,
            issued_at=issued_at,
        )
        self._tokens = new_tokens
        if self._token_updated:
            self._token_updated(new_tokens)
        return self._tokens

    def _raise_for_auth_response(self, status: int, raw: Any) -> None:
        """Raise a typed exception for an auth response."""
        error = ""
        if isinstance(raw, dict):
            error = str(raw.get("error") or raw.get("message") or "")

        if status in {400, 401} or error == "invalid_grant":
            raise VeluxActiveInvalidAuth(error or "Invalid credentials")

        raise VeluxActiveCannotConnect(error or f"Authentication failed with {status}")


class VeluxActiveClient:
    """Thin client combining the VELUX auth flow with pyatmo."""

    def __init__(
        self,
        websession: aiohttp.ClientSession,
        username: str,
        password: str,
        *,
        initial_tokens: OAuthTokens | None = None,
        token_updated: Callable[[OAuthTokens], None] | None = None,
    ) -> None:
        """Initialize the client."""
        self._auth = VeluxActiveAuth(
            websession,
            username=username,
            password=password,
            initial_tokens=initial_tokens,
            token_updated=token_updated,
        )
        self._account = AsyncAccount(self._auth)
        self._data = VeluxActiveData(user=None, homes={}, covers={})
        self._username = username

    async def async_validate(self) -> VeluxActiveAccountInfo:
        """Validate credentials and return basic account info."""
        data = await self.async_update()
        home_names = [home.name for home in data.homes.values()]
        title = home_names[0] if len(home_names) == 1 else self._username
        return VeluxActiveAccountInfo(
            title=title,
            username=self._username,
            home_ids=list(data.homes),
            home_names=home_names,
        )

    async def async_update(self) -> VeluxActiveData:
        """Refresh topology and current status."""
        await self._account.async_update_topology()
        for home_id in list(self._account.homes):
            await self._account.async_update_status(home_id)

        covers = {
            module_id: module
            for home in self._account.homes.values()
            for module_id, module in home.modules.items()
            if isinstance(module, NXO)
        }

        self._data = VeluxActiveData(
            user=self._account.user,
            homes=dict(self._account.homes),
            covers=covers,
        )
        return self._data

    @property
    def data(self) -> VeluxActiveData:
        """Return the latest snapshot."""
        return self._data

    def get_cover(self, module_id: str) -> NXO:
        """Return a cover by module ID."""
        return self._data.covers[module_id]

    @property
    def tokens(self) -> OAuthTokens | None:
        """Return the latest OAuth token set."""
        return self._auth.tokens
