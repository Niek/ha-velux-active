"""The Velux Active with Netatmo integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import OAuthTokens, VeluxActiveClient
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRES_AT,
    CONF_TOKEN_ISSUED_AT,
    PLATFORMS,
)
from .coordinator import VeluxActiveDataUpdateCoordinator

type VeluxActiveConfigEntry = ConfigEntry[VeluxActiveDataUpdateCoordinator]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeluxActiveConfigEntry,
) -> bool:
    """Set up Velux Active with Netatmo from a config entry."""
    def _handle_tokens(tokens: OAuthTokens) -> None:
        updated_data = {**entry.data, **tokens.as_storage_dict()}
        if all(entry.data.get(key) == updated_data.get(key) for key in (
            CONF_ACCESS_TOKEN,
            CONF_REFRESH_TOKEN,
            CONF_TOKEN_EXPIRES_AT,
            CONF_TOKEN_ISSUED_AT,
        )):
            return
        hass.config_entries.async_update_entry(entry, data=updated_data)

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
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: VeluxActiveConfigEntry,
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
