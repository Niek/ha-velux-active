"""Config flow for Velux Active with Netatmo."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    OAuthTokens,
    VeluxActiveCannotConnect,
    VeluxActiveClient,
    VeluxActiveInvalidAuth,
)
from .const import CONF_HASH_SIGN_KEY, CONF_SIGN_KEY_ID, DOMAIN

STEP_USER_DATA_SCHEMA = vol.Schema(
    {vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str}
)

STEP_KEYS_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_HASH_SIGN_KEY, default=""): str,
        vol.Optional(CONF_SIGN_KEY_ID, default=""): str,
    }
)


class VeluxActiveConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for VELUX ACTIVE."""

    VERSION = 1

    _username: str
    _entry_data: dict[str, Any]

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._async_abort_entries_match({CONF_USERNAME: user_input[CONF_USERNAME]})
            try:
                title, tokens = await self._async_validate_input(user_input)
            except VeluxActiveInvalidAuth:
                errors["base"] = "invalid_auth"
            except VeluxActiveCannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
                self._abort_if_unique_id_configured()
                self._username = user_input[CONF_USERNAME]
                self._entry_data = {**user_input, **tokens.as_storage_dict()}
                return await self.async_step_keys()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_keys(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Optional step to enter window signing keys.

        These keys are required for roof window control (open/close/stop/position).
        Roller shutters and blinds work without them.
        See the integration documentation for instructions on obtaining your keys.
        """
        if user_input is not None:
            self._entry_data[CONF_HASH_SIGN_KEY] = user_input.get(CONF_HASH_SIGN_KEY, "").strip()
            self._entry_data[CONF_SIGN_KEY_ID] = user_input.get(CONF_SIGN_KEY_ID, "").strip()
            return self.async_create_entry(
                title=self._username,
                data=self._entry_data,
            )

        return self.async_show_form(
            step_id="keys",
            data_schema=STEP_KEYS_DATA_SCHEMA,
            description_placeholders={},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle a reauthorization flow request."""
        self._username = entry_data[CONF_USERNAME]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reauthentication for an existing config entry."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            full_input = {
                CONF_USERNAME: self._username,
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            try:
                _, tokens = await self._async_validate_input(full_input)
            except VeluxActiveInvalidAuth:
                errors["base"] = "invalid_auth"
            except VeluxActiveCannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        **tokens.as_storage_dict(),
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={CONF_USERNAME: self._username},
            errors=errors,
        )

    async def _async_validate_input(
        self, user_input: Mapping[str, Any]
    ) -> tuple[str, OAuthTokens]:
        """Validate the credentials."""
        client = VeluxActiveClient(
            async_get_clientsession(self.hass),
            user_input[CONF_USERNAME],
            user_input[CONF_PASSWORD],
        )
        info = await client.async_validate()
        tokens = client.tokens
        if tokens is None:
            msg = "VELUX ACTIVE login did not return OAuth tokens"
            raise VeluxActiveCannotConnect(msg)
        return info, tokens
