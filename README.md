# VELUX Active with Netatmo - Home Assistant Integration

A Home Assistant custom integration for VELUX ACTIVE with NETATMO, supporting:

- **Roof windows** - open, close, set position, stop. Requires one-time gateway pairing for signing keys.
- **Roller shutters and awning blinds** - open, close, set position, stop. Works out of the box.

---

## Installation

### Via HACS

1. Open HACS in Home Assistant.
2. Go to **Integrations** -> three-dot menu -> **Custom repositories**.
3. Add this repository URL and select **Integration** as the category.
4. Search for **Velux Active** and install it.
5. Restart Home Assistant.

### Manual

Copy the `velux_active` folder into your `/config/custom_components/` directory and restart Home Assistant.

---

## Setup

1. Go to **Settings -> Devices & Services -> Add Integration**.
2. Search for **Velux Active with Netatmo**.
3. Enter your VELUX ACTIVE account email and password.
4. Choose how to configure roof-window signing keys:
   - **Pair with gateway**: recommended for roof windows.
   - **Manual**: enter an existing **Hash Sign Key** and **Sign Key ID**.
   - **Skip**: use this if you only have roller shutters or blinds.

### Gateway Pairing

Roof windows require cryptographic signing. The integration can retrieve the required signing keys directly from the gateway during setup.

Requirements:

- Home Assistant must be on the same local network as the VELUX gateway.
- Home Assistant must be able to reach the gateway IP address or hostname on TCP port `25050` while pairing.
- You need physical access to the gateway button.

Pairing flow:

1. Select **Pair with gateway** during setup.
2. Enter the gateway local IP address or hostname.
3. Submit the form. Home Assistant asks the VELUX/Netatmo cloud API to put the gateway into key retrieval mode.
4. Wait for the gateway LED to flash.
5. Press the physical gateway button.
6. Submit the next Home Assistant form to retrieve and store the signing keys.

After pairing, roof windows can be opened, closed, stopped, and moved to a specific position.

### Existing Installations

To add or refresh signing keys later, open the integration options in Home Assistant and choose **Pair with gateway** or **Manual**. Do not edit Home Assistant `.storage` files directly.

### Manual Key Fallback

Manual key entry is kept as an advanced fallback if gateway pairing cannot be used on your network. Normal users should not need Android patching, mitmproxy, or manual storage edits.

Advanced/debug signing-key extraction notes are in [`dev/signing-key-extraction.md`](dev/signing-key-extraction.md).

---

## Entities

After setup, the integration creates cover entities for supported devices:

- **Roof windows**: `cover.window_name` - open, close, set position, stop.
- **Roller shutters / awning blinds**: `cover.blind_name` - open, close, set position, stop.

Roof-window movement requires signing keys. Roller shutters and awning blinds work without signing keys.

---

## Window Detection

The VELUX API uses the same module type for some roof windows and shutters. The integration detects roof windows by checking API metadata and common words in the module name, such as `window`, `fenetre`, `fenster`, `raam`, and `finestra`.

If your windows are not detected correctly, add their module IDs to `WINDOW_MODULE_IDS` in `cover.py`.

To find module IDs, enable debug logging:

```yaml
logger:
  logs:
    custom_components.velux_active: debug
```

Then restart Home Assistant and look for log lines like:

```text
Cover entity created: id=aabbcc1122334455 name='Window 1' is_window=True signing=True
```

---

## How Signing Works

The VELUX API requires roof-window commands to be signed with HMAC-SHA512. This prevents unauthorized control of windows.

The signature is computed as:

```text
msg    = f"target_position{position}{timestamp}{nonce}{device_id}"
hash   = HMAC-SHA512(key=base64decode(HashSignKey), msg=msg)
result = base64encode(hash).replace('+', '-').replace('/', '_')
```

When multiple windows are commanded at the same time, commands are sent in a single API call with the same timestamp and incrementing nonces.

---

## Troubleshooting

### Gateway Pairing Fails

Check that:

- Home Assistant and the VELUX gateway are on the same local network.
- The gateway IP address or hostname is correct.
- TCP port `25050` is reachable from Home Assistant while the gateway is in pairing mode.
- The gateway LED is flashing before you press the physical gateway button.
- You press the Home Assistant submit button after pressing the gateway button.

If automatic pairing still fails, use manual key entry with the advanced/debug notes in [`dev/signing-key-extraction.md`](dev/signing-key-extraction.md).

### API Rate Limit Errors

The Netatmo API has rate limits shared across integrations using the same account. If you see errors like:

```text
API limit exceeded. This could be your Application limit or User limit.
```

The account is temporarily throttled. It usually clears within 30-60 minutes. To avoid triggering it:

- Do not restart Home Assistant repeatedly in quick succession.
- Avoid setting a polling interval below 30 seconds.
- Be aware that fast polling after movement commands is cancelled if a rate limit is detected.

### Windows Showing Unknown State On Startup

This can happen if the gateway is temporarily offline or the API is rate limited when Home Assistant starts. The integration retries on the next poll. If windows remain unknown after a few minutes, check that the VELUX gateway has a solid green light and internet access.

### Gateway Goes Offline

If the gateway loses its cloud connection, the VELUX app will usually also be unable to control devices. Power cycling the gateway for 30 seconds often resolves this.

---

## Credits

Based on the original [ha-velux-active](https://github.com/Niek/ha-velux-active) integration by [@Niek](https://github.com/Niek).
