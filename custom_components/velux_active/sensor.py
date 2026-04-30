"""Sensor platform for Velux Active with Netatmo.

Reserved for future sensor entities. Currently the gateway rain state
is exposed via binary_sensor.py. Position sensors (rain_position,
secure_position) are not yet available as pyatmo does not expose
these fields from the homestatus API response.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import VeluxActiveConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VeluxActiveConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Velux Active sensor entities."""
    # No sensor entities currently — reserved for future use
    pass
