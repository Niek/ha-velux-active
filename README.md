# VELUX Active with Netatmo — Home Assistant Integration

A Home Assistant custom integration for VELUX ACTIVE with NETATMO, supporting full control of:

- 🪟 **Roof windows** — open, close, set position, stop (requires one-time key extraction, see below)
- 🪟 **Roller shutters & awning blinds** — open, close, set position, stop (works out of the box)
- 🌡️ **Sensors** — temperature, humidity, CO₂, light intensity (via the VELUX gateway)

-----

## Installation

### Via HACS (recommended)

1. Open HACS in Home Assistant
1. Go to **Integrations** → click the three-dot menu → **Custom repositories**
1. Add this repository URL and select **Integration** as the category
1. Search for **Velux Active** and install it
1. Restart Home Assistant

### Manual

Copy the `velux_active` folder into your `/config/custom_components/` directory and restart Home Assistant.

-----

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
1. Search for **Velux Active with Netatmo**
1. Enter your VELUX ACTIVE account email and password
1. On the next screen you will be asked for a **Hash Sign Key** and **Sign Key ID** — these are required for roof window control. See [Obtaining your window signing keys](#obtaining-your-window-signing-keys) below.
- If you only have roller shutters or blinds, leave these blank and click **Submit**

-----

## Obtaining your window signing keys

Roof windows require cryptographic signing for security — the API verifies that commands come from a paired device. You need to extract two keys from the VELUX app once using a man-in-the-middle proxy. This is a one-time procedure.

### What you need

- An **Android phone** (Android 8 or later) connected to the same WiFi as your VELUX gateway
- A **computer** (Mac or Linux — Windows works too with minor path adjustments)
- Your VELUX ACTIVE gateway powered on and accessible

> **iPhone users:** The iOS app uses certificate pinning that prevents interception. You need an Android device. A cheap secondhand Android phone works fine — you only need it once.

-----

### Step 1 — Install tools on your computer

**Install mitmproxy:**

```bash
# macOS
brew install mitmproxy

# Linux
pip install mitmproxy
```

**Install Android platform tools (for adb):**

```bash
# macOS
brew install android-platform-tools

# Linux
sudo apt install android-tools-adb
```

Verify both are working:

```bash
mitmproxy --version
adb version
```

-----

### Step 2 — Enable USB debugging on your Android phone

1. Go to **Settings → About Phone**
1. Tap **Build number** (or **MIUI version** on Xiaomi) 7 times to enable Developer Options
1. Go to **Settings → Developer Options**
1. Enable **USB Debugging**
1. Connect the phone to your computer via USB and tap **Allow** when prompted

Verify the phone is detected:

```bash
adb devices
```

You should see your device listed.

-----

### Step 3 — Install the patched Velux APK

Download the Velux Active app from Aptoide on your phone, then pull it from the device:

```bash
adb shell pm path com.velux.active
```

This will output something like:

```
package:/data/app/com.velux.active-XXXX==/base.apk
package:/data/app/com.velux.active-XXXX==/split_config.arm64_v8a.apk
package:/data/app/com.velux.active-XXXX==/split_config.en.apk
package:/data/app/com.velux.active-XXXX==/split_config.xxhdpi.apk
```

Pull the base APK (replace the path with your actual path):

```bash
BASE="/data/app/com.velux.active-XXXX=="
adb pull "$BASE/base.apk" ~/velux-base.apk
```

Now decompile, patch, and repack it to trust your proxy certificate:

```bash
# Install apktool
brew install apktool   # macOS
# or: sudo apt install apktool  # Linux

# Decompile
apktool d ~/velux-base.apk -o ~/velux_patched

# Patch network security config to trust user certificates
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

# Disable certificate pinning in the app code
# Find the pin checker class
grep -rn "Certificate pinning failure" ~/velux_patched/smali_classes2/ -l
```

Open the file found above (it will be something like `android/c00.smali`) and find the method:

```
.method public final a(Ljava/lang/String;Ljava/util/List;)V
```

Replace its body with just `return-void` so it looks like:

```smali
.method public final a(Ljava/lang/String;Ljava/util/List;)V
    .locals 1
    .annotation system Ldalvik/annotation/Signature;
        value = {
            "(",
            "Ljava/lang/String;",
            "Ljava/util/List<",
            "+",
            "Ljava/security/cert/Certificate;",
            ">;)V"
        }
    .end annotation

    return-void
.end method
```

Then rebuild and sign the APK:

```bash
# Generate a signing key (one time only)
keytool -genkey -v -keystore ~/velux-key.keystore -alias velux \
  -keyalg RSA -keysize 2048 -validity 10000 \
  -storepass password123 -keypass password123 \
  -dname "CN=Velux, O=Test, C=GB"

# Rebuild
rm -rf ~/velux_patched/build
apktool b ~/velux_patched -o ~/velux-patched.apk

# Sign
apksigner sign \
  --ks ~/velux-key.keystore \
  --ks-pass pass:password123 \
  --key-pass pass:password123 \
  --out ~/velux-signed.apk \
  ~/velux-patched.apk
```

Also sign the split APKs (replace paths with yours):

```bash
apksigner sign --ks ~/velux-key.keystore --ks-pass pass:password123 \
  --key-pass pass:password123 \
  --out ~/split_arm64_signed.apk "$BASE/split_config.arm64_v8a.apk"

apksigner sign --ks ~/velux-key.keystore --ks-pass pass:password123 \
  --key-pass pass:password123 \
  --out ~/split_en_signed.apk "$BASE/split_config.en.apk"

apksigner sign --ks ~/velux-key.keystore --ks-pass pass:password123 \
  --key-pass pass:password123 \
  --out ~/split_xxhdpi_signed.apk "$BASE/split_config.xxhdpi.apk"
```

Uninstall the existing app and install the patched version:

```bash
adb uninstall com.velux.active
adb install-multiple ~/velux-signed.apk ~/split_arm64_signed.apk \
  ~/split_en_signed.apk ~/split_xxhdpi_signed.apk
```

> **Note for Xiaomi / MIUI users:** You may need to disable app verification in Developer Options before the install will succeed.

-----

### Step 4 — Set up mitmproxy

Find your computer’s local IP address:

```bash
# macOS
ipconfig getifaddr en0

# Linux
hostname -I | awk '{print $1}'
```

Start mitmproxy:

```bash
mitmproxy --listen-port 8080 \
  --ignore-hosts "app-ws\.velux-active\.com|googleapis\.com|google\.com|gstatic\.com|crashlytics\.com|firebase\.com|flurry\.com"
```

On your Android phone:

1. Go to **Settings → WiFi** → long-press your network → **Modify network** → **Advanced options**
1. Set **Proxy** to **Manual**
1. **Host:** your computer’s IP address
1. **Port:** `8080`

Install the mitmproxy certificate on your phone:

1. Open Chrome on your phone and go to `http://mitm.it`
1. Tap **Android** and download the certificate
1. Go to **Settings → Security → Install from storage → CA Certificate**
1. Install the downloaded certificate

Set the proxy on your phone via adb as well:

```bash
adb shell settings put global http_proxy YOUR_COMPUTER_IP:8080
```

-----

### Step 5 — Capture the keys

In a second terminal window, start watching the logs:

```bash
adb logcat -s velux-debug:W velux-input:W
```

Open the patched Velux app on your phone and log in. When prompted, press the button on your VELUX gateway to complete authentication.

Once authenticated, **tap a roof window to move it** (open or close it a little).

You should see output like this in your logcat terminal:

```
W velux-debug: AAABBBCCC123ExampleHashSignKeyGoesHere456DDDEEEFFF=
W velux-input: dGFyZ2V0X3Bvc2l0aW9uMjYxNzc3NDk2...
```

Also look in mitmproxy for a `POST /syncapi/v1/setstate` request. Select it and press Enter to view the body — you’ll see:

```json
{
  "sign_key_id": "AAAAAExampleSignKeyId1234Rw==",
  ...
}
```

Your two keys are:

- **Hash Sign Key** — the value logged to `velux-debug` (e.g. `AAABBBCCC123ExampleHashSignKeyGoesHere456...`)
- **Sign Key ID** — the `sign_key_id` value from the mitmproxy request body

-----

### Step 6 — Enter the keys in Home Assistant

You can enter the keys during initial setup (Step 2 of the config flow), or add them to an existing config entry:

**For an existing installation**, run this on your HA host (replace the key values with yours):

```bash
# Docker / Home Assistant OS
docker exec homeassistant python3 -c "
import json
with open('/config/.storage/core.config_entries') as f:
    data = json.load(f)
for entry in data['data']['entries']:
    if 'velux' in entry.get('domain','').lower():
        entry['data']['hash_sign_key'] = 'YOUR_HASH_SIGN_KEY_HERE'
        entry['data']['sign_key_id'] = 'YOUR_SIGN_KEY_ID_HERE'
        print('Updated:', entry['title'])
with open('/config/.storage/core.config_entries', 'w') as f:
    json.dump(data, f)
"
```

Then restart Home Assistant. Your roof windows will now have full control.

-----

### Step 7 — Clean up

Once you have your keys, remove the proxy from your phone:

1. Go to **Settings → WiFi** → your network → **Proxy → None**

```bash
# Remove proxy setting
adb shell settings put global http_proxy :0
```

You can uninstall the patched app and reinstall the regular Velux app from the Play Store. The keys are tied to your gateway pairing and do not change unless you re-pair your gateway.

-----

## Window detection

The integration automatically identifies roof windows by looking for common words in the module name (Window, Fenetre, Fenster, Raam, Finestra). If your windows are not detected correctly, you can add their module IDs to `WINDOW_MODULE_IDS` in `cover.py`.

To find your module IDs, enable debug logging for this integration:

```yaml
# configuration.yaml
logger:
  logs:
    custom_components.velux_active: debug
```

Then restart HA and look for log lines like:

```
Cover entity created: id=aabbcc1122334455 name='Window 1' is_window=True signing=True
```

-----

## How the signing works

The Velux API requires roof window commands to be cryptographically signed using HMAC-SHA512. This prevents unauthorized control of windows (which are openings in your roof and pose a weather/security risk if operated without authorisation).

The signature is computed as:

```
msg    = f"target_position{position}{timestamp}{nonce}{device_id}"
hash   = HMAC-SHA512(key=base64decode(HashSignKey), msg=msg)
result = base64encode(hash).replace('+', '-').replace('/', '_')
```

When multiple windows are commanded simultaneously (e.g. via a group), they are sent in a single API call with incrementing nonces (0, 1, 2, 3…) and the same timestamp — matching the behaviour of the official Velux app.

-----

## Credits

Based on the original [ha-velux-active](https://github.com/Niek/ha-velux-active) integration by [@Niek](https://github.com/Niek).

Window signing support reverse-engineered using mitmproxy and smali patching. Thanks to [@ZTHawk](https://github.com/ZTHawk) for documenting the signing algorithm.
