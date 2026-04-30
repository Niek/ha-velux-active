"""Button platform for Velux Active with Netatmo.

Reserved for future button entities. The secure position button has been
removed as pyatmo does not expose the secure_position field from the
homestatus API response, making it impossible to determine the target
position reliably.
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
    """Set up Velux Active button entities."""
    # No button entities currently — reserved for future use
    pass
