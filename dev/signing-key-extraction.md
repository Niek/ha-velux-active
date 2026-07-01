# Advanced Signing Key Extraction

This document is archival/debug documentation for manually extracting roof-window signing keys.

Normal users should use the Home Assistant gateway pairing flow instead. The pairing flow retrieves signing keys directly from the VELUX gateway and does not require Android patching, mitmproxy, smali editing, or manual Home Assistant storage edits.

Use this document only if:

- Gateway pairing cannot be used on your network.
- You are debugging the signing implementation.
- You understand the risks of installing a patched Android APK and intercepting local app traffic.

Do not edit Home Assistant `.storage/core.config_entries` directly. If you already have keys, enter them through the integration setup flow or options flow.

---

## Standalone Local Retrieval Helper

The repo includes a small helper for exercising the local Netcom key retrieval path outside the Home Assistant UI:

```bash
python3 dev/retrieve-signing-key.py --host GATEWAY_IP_OR_HOSTNAME
```

This helper expects the gateway's local retrieve-key listener to already be active. Trigger pairing from Home Assistant or another client first, wait for the gateway LED to flash, press the physical gateway button, then run the helper.

The helper only talks to the local gateway. It does not log in to VELUX/Netatmo and does not trigger cloud `retrieve_key` mode by itself.

---

## What You Need

- An Android phone connected to the same Wi-Fi as the VELUX gateway.
- A computer with `adb`, `apktool`, `apksigner`, and optionally `mitmproxy`.
- Your VELUX ACTIVE gateway powered on and accessible.

iOS is not practical for this method because the app uses certificate pinning that prevents normal interception.

---

## Install Tools

Install mitmproxy if you want to inspect API traffic:

```bash
# macOS
brew install mitmproxy

# Linux
pip install mitmproxy
```

Install Android platform tools:

```bash
# macOS
brew install android-platform-tools

# Linux
sudo apt install android-tools-adb
```

Install apktool:

```bash
# macOS
brew install apktool

# Linux
sudo apt install apktool
```

`apksigner` is included with Android SDK build tools. Install Android SDK build tools and add them to your `PATH` if `apksigner` is missing.

Verify the tools:

```bash
mitmproxy --version
adb version
apktool --version
apksigner --version
```

---

## Enable USB Debugging

1. On the Android phone, open **Settings -> About Phone**.
2. Tap **Build number** 7 times to enable Developer Options.
3. Open **Settings -> Developer Options**.
4. Enable **USB Debugging**.
5. Connect the phone to the computer by USB and allow the debug prompt.

Verify the phone is detected:

```bash
adb devices
```

---

## Pull And Patch The VELUX APK

Install the regular VELUX ACTIVE app on the phone, then pull its APK files:

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

Patch the network security config to trust user certificates:

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

Find the certificate pinning failure handler:

```bash
grep -rn "Certificate pinning failure" ~/velux_patched/smali* -l
```

Open the matching smali file and find a method like:

```smali
.method public final a(Ljava/lang/String;Ljava/util/List;)V
```

Replace the method body with `return-void`, for example:

```smali
.method public final a(Ljava/lang/String;Ljava/util/List;)V
    .locals 1
    return-void
.end method
```

---

## Optional: Patch The APK To Log Both Keys

If you do not want to use mitmproxy, patch the signing mapper to log both values to logcat.

Search for the signing mapper:

```bash
grep -rn "HashMapperKey" ~/velux_patched/smali* | head
```

In one tested APK version, the mapper was located at:

```text
~/velux_patched/smali/android/br1.smali
```

The exact file and registers can change between app versions. Use nearby `move-result-object` registers when adding log statements.

Add a log after the sign key ID is converted to a string:

```smali
    const-string v3, "velux-key-id"
    invoke-static {v3, v2}, Landroid/util/Log;->w(Ljava/lang/String;Ljava/lang/String;)I
```

Add a log after the hash sign key is converted to a string:

```smali
    const-string v11, "velux-debug"
    invoke-static {v11, v10}, Landroid/util/Log;->w(Ljava/lang/String;Ljava/lang/String;)I
```

Repeat both additions lower in the same mapper file if the APK has two signing paths. Verify the tags exist:

```bash
grep -rn 'velux-key-id\|velux-debug' ~/velux_patched/smali*
```

---

## Rebuild, Sign, And Install The Patched APK

Generate a signing key:

```bash
keytool -genkey -v -keystore ~/velux-key.keystore -alias velux \
  -keyalg RSA -keysize 2048 -validity 10000 \
  -storepass password123 -keypass password123 \
  -dname "CN=Velux, O=Test, C=GB"
```

Rebuild and sign the APK:

```bash
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

If `apktool b` fails with drawable/resource errors, check for empty drawable entries in `res/values/drawables.xml` and replace them with transparent values such as `#00000000`. Some APK versions may also need `android:drawable="@null"` items replaced with transparent shape items.

Sign split APKs if the app uses them:

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

Some Android variants, such as MIUI, may require disabling app verification in Developer Options.

---

## Capture Keys With Logcat

If you patched log statements into the APK, start logcat:

```bash
adb logcat -s velux-key-id:W velux-debug:W
```

Open the patched VELUX app, log in, pair with the gateway, and move a roof window. You should see output like:

```text
W velux-key-id: sign_key_id
W velux-debug: hash_sign_key
```

Use these values in the integration setup/options flow:

- **Hash Sign Key**: the `velux-debug` value.
- **Sign Key ID**: the `velux-key-id` value.

---

## Capture Sign Key ID With mitmproxy

Find your computer's local IP address:

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

On the Android phone:

1. Open Wi-Fi network settings.
2. Set proxy to **Manual**.
3. Set host to your computer IP address.
4. Set port to `8080`.

Install the mitmproxy certificate:

1. Open Chrome on the phone and go to `http://mitm.it`.
2. Tap **Android** and download the certificate.
3. Install it from Android security settings.

You can also set the proxy by adb:

```bash
adb shell settings put global http_proxy YOUR_COMPUTER_IP:8080
```

Open the patched VELUX app, log in, pair with the gateway, and move a roof window. In mitmproxy, inspect a `POST /syncapi/v1/setstate` request. The JSON body contains `sign_key_id`.

If you also patched logcat for the hash key, combine:

- **Hash Sign Key**: `velux-debug` logcat value.
- **Sign Key ID**: `sign_key_id` from the setstate request body.

---

## Clean Up

Remove the proxy from the phone:

```bash
adb shell settings put global http_proxy :0
```

Set Wi-Fi proxy back to **None** in Android network settings.

You can uninstall the patched app and reinstall the regular VELUX app from the Play Store.
