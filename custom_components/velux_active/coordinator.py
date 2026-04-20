"""Coordinator for Velux Active with Netatmo."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from pyatmo.exceptions import ApiError, ApiHomeReachabilityError

from .api import VeluxActiveClient, VeluxActiveData, VeluxActiveCannotConnect, VeluxActiveInvalidAuth
from .const import DOMAIN, LOGGER, UPDATE_INTERVAL

type VeluxActiveConfigEntry = ConfigEntry[VeluxActiveDataUpdateCoordinator]


class VeluxActiveDataUpdateCoordinator(DataUpdateCoordinator[VeluxActiveData]):
    """Poll the Velux Active API through pyatmo."""

    config_entry: VeluxActiveConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: VeluxActiveConfigEntry,
        client: VeluxActiveClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> VeluxActiveData:
        """Fetch the latest account state."""
        try:
            return await self.client.async_update()
        except VeluxActiveInvalidAuth as err:
            raise ConfigEntryAuthFailed("Authentication failed") from err
        except (
            VeluxActiveCannotConnect,
            ApiHomeReachabilityError,
            ApiError,
            TimeoutError,
        ) as err:
            if (data := getattr(self, "data", None)) is not None:
                LOGGER.debug(
                    "Keeping previous Velux Active data after transient update failure: %s",
                    err or type(err).__name__,
                )
                return data
            raise UpdateFailed(
                f"Error communicating with VELUX ACTIVE: {err or type(err).__name__}"
            ) from err
