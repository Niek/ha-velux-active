# Velux Active with Netatmo

Home Assistant custom integration for VELUX ACTIVE with NETATMO and VELUX App Control gateways, exposing supported blinds as cover entities.

This integration uses the VELUX cloud login flow together with `pyatmo` to discover homes and control supported covers.

## Features

- Config flow with email and password
- Reuses stored access and refresh tokens across restarts
- Exposes supported VELUX covers as Home Assistant `cover` entities
- Immediate state updates after commands, with 30 second polling

## Supported Gateways

This integration targets the `NXG` gateway family used by:

- `KIX 300` starter kit for VELUX ACTIVE with NETATMO
- `KIG 300` gateway for VELUX App Control

Tested so far:

- `NXG` gateway
- `NXO` covers, including both `blind` and `awning_blind`

## Installation

### HACS

1. In HACS, add this repository as a custom repository with category `Integration`.
2. Install `Velux Active with Netatmo`.
3. Restart Home Assistant.

### Manual

1. Copy `custom_components/velux_active` into your Home Assistant `custom_components` directory.
2. Restart Home Assistant.

## Configuration

1. Open Home Assistant.
2. Go to `Settings` -> `Devices & Services` -> `Add Integration`.
3. Search for `Velux Active with Netatmo`.
4. Log in with the same email and password you use in the VELUX app.

## Notes

- The integration currently focuses on cover support.
- The underlying `pyatmo` dependency is temporarily pinned to [a fork](https://github.com/Niek/pyatmo) until VELUX support is merged upstream.
- This is an unofficial integration and is not affiliated with VELUX or Netatmo.
