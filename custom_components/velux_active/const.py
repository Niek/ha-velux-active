"""Constants for the Velux Active with Netatmo integration."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.const import Platform

DOMAIN = "velux_active"
MANUFACTURER = "VELUX"
CONTROL_URL = "https://home.netatmo.com/control"
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_TOKEN_EXPIRES_AT = "token_expires_at"
PLATFORMS = [Platform.COVER]
UPDATE_INTERVAL = timedelta(seconds=30)

LOGGER = logging.getLogger(__package__)
