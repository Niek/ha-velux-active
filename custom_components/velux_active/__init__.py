"""The Velux Active with Netatmo integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import OAuthTokens, VeluxActiveClient
from .const import PLATFORMS
from .coordinator import VeluxActiveDataUpdateCoordinator

type VeluxActiveConfigEntry = ConfigEntry[VeluxActiveDataUpdateCoordinator]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeluxActiveConfigEntry,
) -> bool:
    """Set up Velux Active with Netatmo from a config entry."""
    def _handle_tokens(tokens: OAuthTokens) -> None:
        token_data = tokens.as_storage_dict()
        if all(entry.data.get(key) == value for key, value in token_data.items()):
            return
        hass.config_entries.async_update_entry(entry, data={**entry.data, **token_data})

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
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(
    hass: HomeAssistant,
    entry: VeluxActiveConfigEntry,
) -> None:
    """Reload the integration when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant,
    entry: VeluxActiveConfigEntry,
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
