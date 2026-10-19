# ValleyMind Companion — Android

A thin **native Android** shell that turns the existing ValleyMind web app into a
phone app **and** floats the one companion over other apps. It reuses the current
backend, auth, VRM engine and voice — nothing is reimplemented.

> Status: **source complete, not yet compiled or device-verified.** This machine
> has no JDK/Android SDK/Gradle/adb, so no APK has been produced and the
> over-other-apps behaviour has **not** been observed on a real device. See
> "Unverified" below. The web/toggle contract is covered by automated tests.

## How it works

- `MainActivity` — a full-screen `WebView` that loads `BuildConfig.VALLEY_BASE_URL`
  (the same origin serves the UI, the overlay page and the API, so the session
  cookie authenticates everything). It injects `AndroidBridgeJs` into every page
  and hosts the overlay-permission flow.
- `CompanionOverlayService` — a **foreground service** that adds a
  `WindowManager` window of type `TYPE_APPLICATION_OVERLAY`
  (`FLAG_NOT_FOCUSABLE | FLAG_NOT_TOUCH_MODAL`). That window's `WebView` loads
  `/static/companion_overlay.html`, which imports the existing
  `/vrm-preview/assets/companion.js` engine, `/static/cloud_voice.js`, and posts
  to `/api/cloud/chat` — the same brain and voice as the site. A drag handle on
  the HUD moves the window; **Hide** calls `window.vmCompanionAndroid.setVisible(false)`.
- `CompanionBridge` + `AndroidBridgeJs` — expose `window.vmCompanionAndroid` to
  the loaded page:

  ```ts
  setVisible(on: boolean): Promise<boolean>  // resolves the ACTUAL visibility
  getVisible(): boolean
  hasPermission(): boolean
  requestPermission(): void
  onCompanionVisibility(cb: (visible: boolean) => void)  // native -> page
  ```

  `static/settings.js` already selects this object as its `"android-overlay"`
  adapter, so the **existing single Settings toggle** ("Show companion outside
  ValleyMind") drives the native overlay with no separate UI.

## Permission flow (Android 6+)

`SYSTEM_ALERT_WINDOW` is a special permission granted from a system Settings
screen. When the toggle is switched on without it:

1. The switch stays **off** and the embedded companion stays visible.
2. The app opens `Settings.ACTION_MANAGE_OVERLAY_PERMISSION` for the package.
3. On return, `MainActivity.onResume` re-checks the **real** grant via
   `Settings.canDrawOverlays` and reports it back to the page — the requested
   switch is never assumed to have succeeded.

Android 13+ also uses `POST_NOTIFICATIONS` for the foreground-service notice and
`FOREGROUND_SERVICE_SPECIAL_USE` for the service type.

## Build

Requires JDK 17 and the Android SDK (compileSdk 34). Open the `android/` folder
in Android Studio (recommended), or from a machine with Gradle installed:

```bash
cd android
gradle wrapper            # first time only (no wrapper is committed)
./gradlew assembleDebug    # -> android/app/build/outputs/apk/debug/app-debug.apk
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Point the app at a different backend by editing `VALLEY_BASE_URL` in
`android/app/build.gradle`:

- Production: `https://valleymind-ai.onrender.com` (default)
- Local backend from the Android **emulator**: `http://10.0.2.2:5000`
- Local backend from a **device** on the same LAN: `http://<host-ip>:5000`
  (cleartext HTTP is dev-only; add a network-security-config before shipping).

Sign in inside the app once; the session cookie is shared by the app WebView and
the overlay WebView.

## Tests

`python -m pytest tests/test_android_companion.py` — structural checks on the
native sources plus a Node run of `tests/_android_bridge_test.js`, which loads
the **real** `static/settings.js` and asserts adapter selection, permission
gating, confirmed-vs-requested state, failure handling and stale-response
ordering (30 assertions).

## Unverified (needs a build + device)

- Actual compilation / APK generation (no toolchain here).
- The overlay actually drawing over other apps (permission grant + real device).
- Foreground-service lifecycle/heartbeat, drag behaviour, cookie sharing in the
  overlay WebView on real Android.
- Play Console policy review for `SYSTEM_ALERT_WINDOW` / `specialUse` FGS.
