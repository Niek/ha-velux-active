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
CONF_HASH_SIGN_KEY = "hash_sign_key"
CONF_SIGN_KEY_ID = "sign_key_id"
PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.COVER, Platform.LOCK, Platform.SENSOR, Platform.SWITCH]
UPDATE_INTERVAL = timedelta(seconds=30)

VELUX_API_URL = "https://app.velux-active.com"
VELUX_APP_TYPE = "app_velux"
VELUX_APP_VERSION = "791112100"

LOGGER = logging.getLogger(__package__)
