"""The Velux Active with Netatmo integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import OAuthTokens, VeluxActiveClient
from .const import CONF_HASH_SIGN_KEY, CONF_SIGN_KEY_ID
from .coordinator import VeluxActiveDataUpdateCoordinator

type VeluxActiveConfigEntry = ConfigEntry[VeluxActiveDataUpdateCoordinator]

PLATFORMS = [Platform.COVER, Platform.SWITCH]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeluxActiveConfigEntry,
) -> bool:
    """Set up Velux Active with Netatmo from a config entry."""
    signing_keys = _entry_signing_keys(entry)

    def _handle_tokens(tokens: OAuthTokens) -> None:
        token_data = tokens.as_storage_dict()
        if all(entry.data.get(key) == value for key, value in token_data.items()):
            return
        hass.config_entries.async_update_entry(entry, data={**entry.data, **token_data})

    # Merge options (signing keys) into data so all code reads from one place
    if entry.options:
        merged_data = {**entry.data, **entry.options}
        hass.config_entries.async_update_entry(entry, data=merged_data, options={})

    coordinator = VeluxActiveDataUpdateCoordinator(
        hass,
        entry,
        VeluxActiveClient(
            async_get_clientsession(hass),
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
            initial_tokens=OAuthTokens.from_mapping(entry.data),
            token_updated=_handle_tokens,
        ),
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _async_update_listener(
        hass: HomeAssistant,
        updated_entry: VeluxActiveConfigEntry,
    ) -> None:
        """Reload only when signing-key options change."""
        nonlocal signing_keys
        updated_signing_keys = _entry_signing_keys(updated_entry)
        if updated_signing_keys == signing_keys:
            return
        signing_keys = updated_signing_keys
        await hass.config_entries.async_reload(updated_entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


def _entry_signing_keys(entry: VeluxActiveConfigEntry) -> tuple[str, str]:
    """Return signing keys from options if present, otherwise entry data."""
    return (
        entry.options.get(CONF_HASH_SIGN_KEY, entry.data.get(CONF_HASH_SIGN_KEY, "")),
        entry.options.get(CONF_SIGN_KEY_ID, entry.data.get(CONF_SIGN_KEY_ID, "")),
    )


async def async_unload_entry(
    hass: HomeAssistant,
    entry: VeluxActiveConfigEntry,
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
