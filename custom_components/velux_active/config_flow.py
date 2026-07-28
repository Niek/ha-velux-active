"""Config flow for Velux Active with Netatmo."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    OAuthTokens,
    VeluxActiveCannotConnect,
    VeluxActiveClient,
    VeluxActiveInvalidAuth,
)
from .const import (
    CONF_HASH_SIGN_KEY,
    CONF_SIGN_KEY_GATEWAY_ID,
    CONF_SIGN_KEY_ID,
    DOMAIN,
    LOGGER,
)
from .pairing import VeluxPairingError, retrieve_signing_key

STEP_USER_DATA_SCHEMA = vol.Schema(
    {vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str}
)

STEP_KEYS_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_HASH_SIGN_KEY, default=""): str,
        vol.Optional(CONF_SIGN_KEY_ID, default=""): str,
    }
)

PAIRING_METHOD_AUTO = "auto"
PAIRING_METHOD_MANUAL = "manual"
PAIRING_METHOD_SKIP = "skip"
FIELD_GATEWAY_HOST = "gateway_host"
FIELD_GATEWAY = "gateway"

STEP_PAIRING_METHOD_SCHEMA = vol.Schema(
    {
        vol.Required("pairing_method", default=PAIRING_METHOD_AUTO): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(
                        value=PAIRING_METHOD_AUTO,
                        label="Pair with gateway",
                    ),
                    selector.SelectOptionDict(
                        value=PAIRING_METHOD_MANUAL,
                        label="Enter keys manually",
                    ),
                    selector.SelectOptionDict(
                        value=PAIRING_METHOD_SKIP,
                        label="Skip roof-window signing",
                    ),
                ],
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
    }
)

STEP_PAIR_SCHEMA = vol.Schema({vol.Required(FIELD_GATEWAY_HOST): str})
STEP_PAIR_BUTTON_SCHEMA = vol.Schema({})


class VeluxActiveConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for VELUX ACTIVE."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> VeluxActiveOptionsFlow:
        """Return the options flow handler."""
        return VeluxActiveOptionsFlow()

    _username: str
    _client: VeluxActiveClient
    _entry_data: dict[str, Any]
    _pair_gateway_choices: dict[str, tuple[str, str, str]]
    _pair_gateway_host: str
    _pair_gateway_id: str
    _pair_home_id: str

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._async_abort_entries_match({CONF_USERNAME: user_input[CONF_USERNAME]})
            try:
                title, tokens, client = await self._async_validate_input(user_input)
            except VeluxActiveInvalidAuth:
                errors["base"] = "invalid_auth"
            except VeluxActiveCannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                LOGGER.exception("Unexpected error validating Velux Active account")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
                self._abort_if_unique_id_configured()
                self._username = user_input[CONF_USERNAME]
                self._client = client
                self._entry_data = {**user_input, **tokens.as_storage_dict()}
                return await self.async_step_pairing_method()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_pairing_method(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose how to configure window signing keys."""
        if user_input is not None:
            method = user_input["pairing_method"]
            if method == PAIRING_METHOD_AUTO:
                return await self.async_step_select_gateway()
            if method == PAIRING_METHOD_MANUAL:
                return await self.async_step_keys()

            self._entry_data[CONF_HASH_SIGN_KEY] = ""
            self._entry_data[CONF_SIGN_KEY_ID] = ""
            return self.async_create_entry(title=self._username, data=self._entry_data)

        return self.async_show_form(
            step_id="pairing_method",
            data_schema=STEP_PAIRING_METHOD_SCHEMA,
        )

    async def async_step_select_gateway(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select which gateway should provide signing keys."""
        errors: dict[str, str] = {}

        if not hasattr(self, "_pair_gateway_choices"):
            try:
                self._pair_gateway_choices = _get_gateway_choices(
                    await self._client.async_get_raw_homesdata()
                )
            except (VeluxActiveCannotConnect, VeluxPairingError) as err:
                LOGGER.warning("Failed to load VELUX gateways for pairing: %s", err)
                errors["base"] = "pairing_failed"
            except Exception:
                LOGGER.exception("Unexpected error loading Velux Active gateways")
                errors["base"] = "unknown"

        if not errors and user_input is not None:
            self._set_pairing_gateway(user_input[FIELD_GATEWAY])
            return await self.async_step_pair()

        if not errors and len(self._pair_gateway_choices) == 1:
            self._set_pairing_gateway(next(iter(self._pair_gateway_choices)))
            return await self.async_step_pair()

        return self.async_show_form(
            step_id="select_gateway",
            data_schema=_gateway_selection_schema(
                getattr(self, "_pair_gateway_choices", {})
            ),
            errors=errors,
        )

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the gateway to start local pairing mode."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._pair_gateway_host = user_input[FIELD_GATEWAY_HOST].strip()
            try:
                await self._async_trigger_pairing(self._client)
            except (VeluxActiveCannotConnect, VeluxPairingError) as err:
                LOGGER.warning("Failed to start VELUX gateway pairing: %s", err)
                errors["base"] = "pairing_failed"
            except Exception:
                LOGGER.exception("Unexpected error starting Velux Active pairing")
                errors["base"] = "unknown"
            else:
                return await self.async_step_pair_button()

        return self.async_show_form(
            step_id="pair",
            data_schema=STEP_PAIR_SCHEMA,
            errors=errors,
        )

    async def async_step_pair_button(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Retrieve signing keys after the gateway button was pressed."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                signing_key = await self._async_retrieve_pairing_key(self._pair_gateway_host)
            except VeluxPairingError as err:
                LOGGER.warning("VELUX gateway pairing failed: %s", err)
                errors["base"] = "pairing_failed"
            except Exception:
                LOGGER.exception("Unexpected error during Velux Active pairing")
                errors["base"] = "unknown"
            else:
                self._entry_data[CONF_HASH_SIGN_KEY] = signing_key.hash_sign_key
                self._entry_data[CONF_SIGN_KEY_ID] = signing_key.sign_key_id
                self._entry_data[CONF_SIGN_KEY_GATEWAY_ID] = self._pair_gateway_id
                return self.async_create_entry(title=self._username, data=self._entry_data)

        return self.async_show_form(
            step_id="pair_button",
            data_schema=STEP_PAIR_BUTTON_SCHEMA,
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
            self._entry_data[CONF_SIGN_KEY_GATEWAY_ID] = ""
            return self.async_create_entry(title=self._username, data=self._entry_data)

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
                _, tokens, _ = await self._async_validate_input(full_input)
            except VeluxActiveInvalidAuth:
                errors["base"] = "invalid_auth"
            except VeluxActiveCannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:
                LOGGER.exception("Unexpected error reauthenticating Velux Active account")
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
    ) -> tuple[str, OAuthTokens, VeluxActiveClient]:
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
        return info, tokens, client

    async def _async_trigger_pairing(self, client: VeluxActiveClient) -> None:
        """Trigger gateway key retrieval."""
        await client.async_trigger_retrieve_key(self._pair_home_id, self._pair_gateway_id)

    async def _async_retrieve_pairing_key(self, host: str):
        """Fetch the key from the local gateway listener."""
        return await self.hass.async_add_executor_job(lambda: retrieve_signing_key(host=host))

    def _set_pairing_gateway(self, choice: str) -> None:
        """Store selected pairing gateway identifiers."""
        self._pair_home_id, self._pair_gateway_id, _ = self._pair_gateway_choices[choice]


class VeluxActiveOptionsFlow(OptionsFlow):
    """Handle options for an existing Velux Active config entry.

    Allows users to update their window signing keys without deleting
    and re-adding the integration — useful after re-pairing the gateway.
    """

    _pair_gateway_host: str
    _pair_gateway_choices: dict[str, tuple[str, str, str]]
    _pair_gateway_id: str
    _pair_home_id: str

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the options step."""
        if user_input is not None:
            method = user_input["pairing_method"]
            if method == PAIRING_METHOD_AUTO:
                return await self.async_step_select_gateway()
            if method == PAIRING_METHOD_SKIP:
                return self.async_create_entry(
                    title="", data={CONF_HASH_SIGN_KEY: "", CONF_SIGN_KEY_ID: ""}
                )
            return await self.async_step_keys()

        return self.async_show_form(
            step_id="init",
            data_schema=STEP_PAIRING_METHOD_SCHEMA,
        )

    async def async_step_select_gateway(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select which gateway should provide signing keys."""
        errors: dict[str, str] = {}
        entry_data = self.config_entry.data
        client = VeluxActiveClient(
            async_get_clientsession(self.hass),
            entry_data[CONF_USERNAME],
            entry_data[CONF_PASSWORD],
            initial_tokens=OAuthTokens.from_mapping(entry_data),
        )

        if not hasattr(self, "_pair_gateway_choices"):
            try:
                self._pair_gateway_choices = _get_gateway_choices(
                    await client.async_get_raw_homesdata()
                )
            except (VeluxActiveCannotConnect, VeluxPairingError) as err:
                LOGGER.warning("Failed to load VELUX gateways for pairing: %s", err)
                errors["base"] = "pairing_failed"
            except Exception:
                LOGGER.exception("Unexpected error loading Velux Active options gateways")
                errors["base"] = "unknown"

        if not errors and user_input is not None:
            self._set_pairing_gateway(user_input[FIELD_GATEWAY])
            return await self.async_step_pair()

        if not errors and len(self._pair_gateway_choices) == 1:
            self._set_pairing_gateway(next(iter(self._pair_gateway_choices)))
            return await self.async_step_pair()

        return self.async_show_form(
            step_id="select_gateway",
            data_schema=_gateway_selection_schema(
                getattr(self, "_pair_gateway_choices", {})
            ),
            errors=errors,
        )

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Automatically refresh signing keys."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._pair_gateway_host = user_input[FIELD_GATEWAY_HOST].strip()
            entry_data = self.config_entry.data
            client = VeluxActiveClient(
                async_get_clientsession(self.hass),
                entry_data[CONF_USERNAME],
                entry_data[CONF_PASSWORD],
                initial_tokens=OAuthTokens.from_mapping(entry_data),
            )
            try:
                await self._async_trigger_pairing(client)
            except (VeluxActiveCannotConnect, VeluxPairingError) as err:
                LOGGER.warning("Failed to start VELUX gateway pairing: %s", err)
                errors["base"] = "pairing_failed"
            except Exception:
                LOGGER.exception("Unexpected error starting Velux Active options pairing")
                errors["base"] = "unknown"
            else:
                return await self.async_step_pair_button()

        return self.async_show_form(
            step_id="pair",
            data_schema=STEP_PAIR_SCHEMA,
            errors=errors,
        )

    async def async_step_pair_button(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Retrieve refreshed signing keys after the gateway button was pressed."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                signing_key = await self._async_retrieve_pairing_key(self._pair_gateway_host)
            except VeluxPairingError as err:
                LOGGER.warning("VELUX gateway pairing failed: %s", err)
                errors["base"] = "pairing_failed"
            except Exception:
                LOGGER.exception("Unexpected error during Velux Active options pairing")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_HASH_SIGN_KEY: signing_key.hash_sign_key,
                        CONF_SIGN_KEY_ID: signing_key.sign_key_id,
                        CONF_SIGN_KEY_GATEWAY_ID: self._pair_gateway_id,
                    },
                )

        return self.async_show_form(
            step_id="pair_button",
            data_schema=STEP_PAIR_BUTTON_SCHEMA,
            errors=errors,
        )

    async def async_step_keys(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual key entry."""
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_HASH_SIGN_KEY: user_input.get(CONF_HASH_SIGN_KEY, "").strip(),
                    CONF_SIGN_KEY_ID: user_input.get(CONF_SIGN_KEY_ID, "").strip(),
                    CONF_SIGN_KEY_GATEWAY_ID: "",
                },
            )

        current_key = self.config_entry.data.get(
            CONF_HASH_SIGN_KEY,
            self.config_entry.options.get(CONF_HASH_SIGN_KEY, ""),
        )
        current_id = self.config_entry.data.get(
            CONF_SIGN_KEY_ID,
            self.config_entry.options.get(CONF_SIGN_KEY_ID, ""),
        )

        return self.async_show_form(
            step_id="keys",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_HASH_SIGN_KEY, default=current_key): str,
                    vol.Optional(CONF_SIGN_KEY_ID, default=current_id): str,
                }
            ),
        )

    async def _async_trigger_pairing(self, client: VeluxActiveClient) -> None:
        """Trigger gateway key retrieval."""
        await client.async_trigger_retrieve_key(self._pair_home_id, self._pair_gateway_id)

    async def _async_retrieve_pairing_key(self, host: str):
        """Fetch the key from the local gateway listener."""
        return await self.hass.async_add_executor_job(lambda: retrieve_signing_key(host=host))

    def _set_pairing_gateway(self, choice: str) -> None:
        """Store selected pairing gateway identifiers."""
        self._pair_home_id, self._pair_gateway_id, _ = self._pair_gateway_choices[choice]


def _get_gateway_choices(raw: Mapping[str, Any]) -> dict[str, tuple[str, str, str]]:
    """Return available VELUX gateways from raw homesdata."""
    choices: dict[str, tuple[str, str, str]] = {}
    for home in raw.get("body", {}).get("homes", []):
        home_id = str(home.get("id") or "")
        home_name = str(home.get("name") or home_id)
        for module in home.get("modules", []):
            if module.get("type") == "NXG":
                gateway_id = str(module.get("id") or "")
                gateway_name = str(module.get("name") or gateway_id)
                choice = f"{home_id}:{gateway_id}"
                choices[choice] = (home_id, gateway_id, f"{home_name} / {gateway_name}")
    if not choices:
        raise VeluxPairingError("No VELUX gateway found")
    return choices


def _gateway_selection_schema(
    choices: Mapping[str, tuple[str, str, str]],
) -> vol.Schema:
    """Build the gateway selection schema."""
    if not choices:
        return vol.Schema({})
    return vol.Schema(
        {
            vol.Required(FIELD_GATEWAY): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=value, label=label)
                        for value, (_, _, label) in choices.items()
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        }
    )
