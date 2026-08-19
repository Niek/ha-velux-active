"""Coordinator for Velux Active with Netatmo."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pyatmo.exceptions import ApiError, ApiHomeReachabilityError

from .api import (
    VeluxActiveCannotConnect,
    VeluxActiveClient,
    VeluxActiveData,
    VeluxActiveInvalidAuth,
)
from .const import DOMAIN, LOGGER, UPDATE_INTERVAL

type VeluxActiveConfigEntry = ConfigEntry[VeluxActiveDataUpdateCoordinator]

FAST_POLL_INTERVAL = timedelta(seconds=15)
FAST_POLL_DURATION = 45  # seconds of fast polling after a movement command

# Number of consecutive failures before marking entities unavailable.
# This prevents brief token refresh failures from flickering entities to
# unavailable — a single failed poll is kept silently using the previous data.
_FAILURE_THRESHOLD = 3


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
        self._fast_poll_task: asyncio.Task | None = None
        self._realtime_task: asyncio.Task[None] | None = None
        self._topology_loaded: bool = False
        self._consecutive_failures: int = 0

    def start_realtime(self) -> None:
        """Start listening for realtime cover updates."""
        if self._realtime_task is not None and not self._realtime_task.done():
            return
        self._realtime_task = self.config_entry.async_create_background_task(
            self.hass,
            self._async_listen_realtime(),
            f"{DOMAIN} websocket",
        )

    async def async_stop_realtime(self) -> None:
        """Stop the realtime listener."""
        task = self._realtime_task
        self._realtime_task = None
        if task is None or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _async_listen_realtime(self) -> None:
        """Merge realtime cover events into the current coordinator data."""
        async for event in self.client.async_realtime_events():
            if self.client.apply_realtime_cover_event(event):
                self.async_set_updated_data(self.data)

    def start_fast_polling(self) -> None:
        """Switch to fast polling for FAST_POLL_DURATION seconds after a movement."""
        if self._fast_poll_task is not None and not self._fast_poll_task.done():
            self._fast_poll_task.cancel()

        self.update_interval = FAST_POLL_INTERVAL
        self._fast_poll_task = asyncio.create_task(self._revert_polling_after_delay())

    async def _revert_polling_after_delay(self) -> None:
        """Revert to normal polling interval after fast poll duration expires."""
        await asyncio.sleep(FAST_POLL_DURATION)
        self.update_interval = UPDATE_INTERVAL
        LOGGER.debug("Reverted to normal polling interval")

    async def _async_update_data(self) -> VeluxActiveData:
        """Fetch the latest account state.

        Transient failures (e.g. token refresh, brief network blip) are
        tolerated up to _FAILURE_THRESHOLD consecutive times before entities
        are marked unavailable. This prevents the token refresh cycle
        from causing flickering unavailable states in history.
        """
        try:
            result = await self._async_fetch_data()
            self._consecutive_failures = 0  # Reset on success
            return result

        except ApiError as err:
            if _is_invalid_token_error(err):
                LOGGER.debug("Stored access token was rejected; logging in again")
                await self.client.async_reauthenticate()
                self._topology_loaded = False
                try:
                    result = await self._async_fetch_data()
                    self._consecutive_failures = 0
                    return result
                except ApiError as retry_err:
                    err = retry_err

            return self._handle_update_error(err)

        except VeluxActiveInvalidAuth as err:
            raise ConfigEntryAuthFailed("Authentication failed") from err

        except (
            VeluxActiveCannotConnect,
            ApiHomeReachabilityError,
            TimeoutError,
        ) as err:
            return self._handle_update_error(err)

    async def _async_fetch_data(self) -> VeluxActiveData:
        """Fetch topology if needed, then return current data."""
        if not self._topology_loaded:
            await self.client.async_setup()
            self._topology_loaded = True
        return await self.client.async_update()

    def _handle_update_error(self, err: Exception) -> VeluxActiveData:
        """Handle transient update errors using the existing failure threshold."""
        err_str = str(err)

        # If rate limited, back off immediately
        if "429" in err_str or "API limit" in err_str:
            if self._fast_poll_task is not None and not self._fast_poll_task.done():
                self._fast_poll_task.cancel()
            self.update_interval = UPDATE_INTERVAL
            LOGGER.debug("Rate limited — reverting to normal polling interval")
            self._topology_loaded = False

        self._consecutive_failures += 1
        previous_data = getattr(self, "data", None)

        if previous_data is not None:
            if self._consecutive_failures < _FAILURE_THRESHOLD:
                LOGGER.debug(
                    "Transient failure %d/%d, keeping previous data: %s",
                    self._consecutive_failures,
                    _FAILURE_THRESHOLD,
                    err or type(err).__name__,
                )
                return previous_data
            LOGGER.warning(
                "Velux Active update failed %d times in a row: %s",
                self._consecutive_failures,
                err or type(err).__name__,
            )

        raise UpdateFailed(
            f"Error communicating with VELUX ACTIVE: {err or type(err).__name__}"
        ) from err


def _is_invalid_token_error(err: Exception) -> bool:
    """Return True if Netatmo rejected the stored access token."""
    err_str = str(err).lower()
    return "invalid access token" in err_str or (
        "403" in err_str and "access token" in err_str
    )
