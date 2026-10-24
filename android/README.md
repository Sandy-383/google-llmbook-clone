# LLMBook for Android

An Android app (a [Trusted Web Activity](https://developer.chrome.com/docs/android/trusted-web-activity)) that opens this server's LLMBook full-screen, using the phone's Chrome engine. It is built with Google's [Bubblewrap](https://github.com/GoogleChromeLabs/bubblewrap) from `twa-manifest.json`.

Everything else in this folder is generated or secret and git-ignored:
- `signing/`: the signing key and its password
- the generated Gradle project
- `public/`: what Caddy serves

## The signing key: back it up

`signing/llmbook.keystore` and `signing/PASSWORD.txt` were generated for this app. **Android only installs an update if it is signed with the same key**, so keep a copy of both somewhere safe outside this laptop. If the key is lost, friends must uninstall the app before installing a newly signed one.

## Build

Toolchain, installed outside the repo under `D:\android-tools`:
- JDK 17 (Temurin)
- Android command-line tools, build-tools 36.1.0, platform 36
- Bubblewrap

`%USERPROFILE%\.bubblewrap\config.json` points Bubblewrap at the JDK and SDK.

```powershell
cd android
$pw = ((Get-Content signing\PASSWORD.txt | Select-String '^Password').Line -split ': ')[-1].Trim()
$env:BUBBLEWRAP_KEYSTORE_PASSWORD = $pw; $env:BUBBLEWRAP_KEY_PASSWORD = $pw
$env:GRADLE_USER_HOME = "D:\android-tools\gradle-home"   # keeps Gradle's cache off C:
D:\android-tools\bubblewrap\node_modules\.bin\bubblewrap.cmd update --skipVersionUpgrade   # after editing twa-manifest.json
D:\android-tools\bubblewrap\node_modules\.bin\bubblewrap.cmd build --skipPwaValidation
```

Then publish the outputs where Caddy serves them:
- `app-release-signed.apk` → `public/download/LLMBook.apk` (offered at `/get-app`)
- `assetlinks.json` → `public/.well-known/assetlinks.json`

That second file proves the app and the site belong together; without it, Android shows a browser bar at the top of the app.

To release an update:
1. Raise `appVersionCode` (and `appVersion`) in `twa-manifest.json`.
2. Rebuild and publish as above.

Installed apps don't update themselves; friends download the new APK from `/get-app` and install it over the old one.

## If the server's address changes

The app is tied to `host` in `twa-manifest.json` (the permanent Tailscale Funnel address). With a new address, change `host` and the URLs that contain it, raise `appVersionCode`, then rebuild and redistribute.
