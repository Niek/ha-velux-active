# Velux Active with Netatmo

Home Assistant custom integration for VELUX ACTIVE with NETATMO and VELUX App Control gateways, exposing supported blinds, shutters, and roof windows as cover entities.

This integration uses the VELUX cloud login flow together with `pyatmo` to discover homes and control supported covers.

## Features

- Config flow with email and password
- Optional signing-key setup for VELUX roof windows
- Reuses stored access and refresh tokens across restarts
- Exposes supported VELUX covers as Home Assistant `cover` entities
- Immediate state updates after commands, with 30 second polling

## Supported Gateways

This integration targets the `NXG` gateway family used by:

- `KIX 300` starter kit for VELUX ACTIVE with NETATMO
- `KIG 300` gateway for VELUX App Control

Tested so far:

- `NXG` gateway
- `NXO` covers, including `blind`, `awning_blind`, and roof windows

## Installation

### HACS

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Niek&repository=ha-velux-active)

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
5. If you have roof windows, enter the optional signing keys. Blinds and shutters work without them.

## Roof Window Signing Keys

Roof windows require cryptographic signing for security. The VELUX API verifies that commands come from a paired device before it allows a roof window to open or close.

You need to extract two values from the VELUX Android app once:

- **Hash Sign Key**: used to compute the command signature.
- **Sign Key ID**: sent with signed window commands.

There are two known extraction methods:

- **Method A: mitmproxy** captures `sign_key_id` from the API request and reads `hash_sign_key` from app logs.
- **Method B: patched APK logcat** logs both values directly from the patched app.

You need an Android phone connected to the same Wi-Fi as your VELUX gateway and a computer with `adb`, `apktool`, and `apksigner`. Install `mitmproxy` too if you want to use Method A.

The iOS app cannot be used for this extraction flow because of certificate pinning.

### Install Tools

```bash
# macOS
brew install android-platform-tools apktool
brew install mitmproxy  # Method A only

# Linux
sudo apt install android-tools-adb apktool
pip install mitmproxy  # Method A only
```

Enable USB debugging on the Android phone, connect it by USB, and verify it is visible:

```bash
adb devices
```

### Pull And Patch The APK

Install the VELUX ACTIVE app on the phone, then pull its APK files:

```bash
mkdir -p ~/velux-apks

adb shell pm path com.velux.active | tr -d '\r' | while IFS= read -r line; do
  apk="${line#package:}"
  adb pull "$apk" "$HOME/velux-apks/$(basename "$apk")"
done
```

Decompile the base APK:

```bash
apktool d ~/velux-apks/base.apk -o ~/velux_patched
```

Choose one of the patch methods below.

### Method A: mitmproxy Patch

Patch the app so it trusts the mitmproxy certificate:

```bash
cat > ~/velux_patched/res/xml/network_security_config.xml << 'EOF'
<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
    <base-config cleartextTrafficPermitted="true">
        <trust-anchors>
            <certificates src="system"/>
            <certificates src="user"/>
        </trust-anchors>
    </base-config>
    <domain-config cleartextTrafficPermitted="true">
        <domain includeSubdomains="true">fw.netatmo.net</domain>
        <trustkit-config disableDefaultReportUri="true" enforcePinning="false">
            <report-uri>https://cert-pinning.netatmo.com/</report-uri>
        </trustkit-config>
    </domain-config>
</network-security-config>
EOF
```

Disable certificate pinning in the app code:

```bash
grep -rn "Certificate pinning failure" ~/velux_patched/smali* -l
```

Open the file found above, find the certificate-check method, and replace its body with `return-void`.

### Method B: Patched APK Logcat

Patch the signing code to log the key values. In the tested APK, this code is in:

```bash
~/velux_patched/smali/android/br1.smali
```

The exact filename can change between app versions, so search for the signing mapper if needed:

```bash
grep -rn "HashMapperKey" ~/velux_patched/smali* | head
```

Add Android log statements around the signing key values:

- `velux-key-id`: log the Sign Key ID value.
- `velux-debug`: log the Hash Sign Key value.

After patching, verify the tags exist:

```bash
grep -rn 'velux-key-id\|velux-debug' ~/velux_patched/smali*
```

### Rebuild And Install

Generate a signing key, rebuild, and sign the patched base APK:

```bash
keytool -genkey -v -keystore ~/velux-key.keystore -alias velux \
  -keyalg RSA -keysize 2048 -validity 10000 \
  -storepass password123 -keypass password123 \
  -dname "CN=Velux, O=Test, C=GB"

rm -rf ~/velux_patched/build
apktool b ~/velux_patched -o ~/velux-patched.apk

mkdir -p ~/velux-signed
apksigner sign \
  --ks ~/velux-key.keystore \
  --ks-pass pass:password123 \
  --key-pass pass:password123 \
  --out ~/velux-signed/base.apk \
  ~/velux-patched.apk
```

Sign any split APKs pulled from the device:

```bash
for apk in ~/velux-apks/split_config*.apk; do
  [ -e "$apk" ] || continue
  apksigner sign \
    --ks ~/velux-key.keystore \
    --ks-pass pass:password123 \
    --key-pass pass:password123 \
    --out "$HOME/velux-signed/$(basename "$apk")" \
    "$apk"
done
```

Install the patched app:

```bash
adb uninstall com.velux.active

install_apks=(~/velux-signed/base.apk)
for apk in ~/velux-signed/split_config*.apk; do
  [ -e "$apk" ] || continue
  install_apks+=("$apk")
done

adb install-multiple "${install_apks[@]}"
```

### Capture Keys With Method A

Start mitmproxy:

```bash
mitmproxy --listen-port 8080 \
  --ignore-hosts "app-ws\.velux-active\.com|googleapis\.com|google\.com|gstatic\.com|crashlytics\.com|firebase\.com|flurry\.com"
```

Set the Android Wi-Fi proxy to your computer on port `8080`, install the certificate from `http://mitm.it`, and optionally set the proxy with adb:

```bash
adb shell settings put global http_proxy YOUR_COMPUTER_IP:8080
```

Watch the app logs:

```bash
adb logcat -s velux-debug:W velux-input:W
```

Open the patched VELUX app, log in, press the gateway button when prompted, and move a roof window a little.

Use these values:

- **Hash Sign Key**: the `velux-debug` log value.
- **Sign Key ID**: the `sign_key_id` value in the mitmproxy `POST /syncapi/v1/setstate` request body.

When finished, remove the proxy:

```bash
adb shell settings put global http_proxy :0
```

### Capture Keys With Method B

Watch the patched app logs:

```bash
adb logcat -s velux-key-id:W velux-debug:W
```

Open the patched VELUX app, log in, press the gateway button when prompted, and move a roof window a little.

Use these values:

- **Hash Sign Key**: the `velux-debug` log value.
- **Sign Key ID**: the `velux-key-id` log value.

After extracting the keys, you can uninstall the patched app and reinstall the regular VELUX app. The keys are tied to your gateway pairing and do not change unless the gateway is re-paired.

## Notes

- The integration currently focuses on cover support.
- Roof window support requires signing keys. Roller shutters and blinds do not.
- This is an unofficial integration and is not affiliated with VELUX or Netatmo.
