# TruckBuddy app

## Builds

Three ways the app runs, and only the first two are the real binary:

- Dev client (`eas build --profile development`): our own build with every native module in `package.json`
  (MapLibre, background location, keep-awake, Sentry), plus Metro, hot reload and the dev menu. This is what
  the stand and test phones should run; see `docs/stand-dev-client.md`.
- Staging / production (`eas build --profile staging|production`): what drivers get. Internal distribution
  for the pilot fleets, the store for the rest.
- Expo Go: works for quick UI checks only; nothing background, no MapLibre, a different binary from drivers'.

OTA: `runtimeVersion` follows the app version, so every build of one version is one runtime (1.0.0 was the first
store build; 1.1.0 adds the sign-in modules and is a new runtime: an update published for 1.1.0 never reaches a
1.0.0 install). A JS-only change ships
with `eas update --branch staging` (or `production`) and is picked up on the next app open; a native change
(new module, config plugin) needs a new build and a version bump. Publish with `eas update --branch <channel> --environment <env>`: `eas update` ignores the `env` in `eas.json`, so a local `.env.local` (an emulator backend URL) would ship to drivers. Keep `EXPO_PUBLIC_MAP_PROVIDER` the same as the build's, or the map screen crashes.

Crash reporting: Sentry is initialised in `app/_layout.tsx` and dormant without `EXPO_PUBLIC_SENTRY_DSN`.
Events carry the `trip_id` tag so a crash sits next to the trip log on the backend.

One-time owner steps: `npx eas-cli login` (project `@andrylik/truckbuddy` is already set in `app.config.ts`),
Sentry project (DSN, `SENTRY_ORG`, `SENTRY_PROJECT`, `SENTRY_AUTH_TOKEN` as EAS secrets), `EXPO_TOKEN` as a
GitHub secret for the manual `eas-build-dev` workflow job.

## Sign-in (Apple and Google)

Since 1.1.0 the app opens with language → intro → sign-in → consent → the app. Sign-in is required: the truck,
the places and the trips belong to the account and come back on a new phone. The code is in `src/signin/` (the
providers), `src/components/SignInGate.tsx` (the screen), `src/state/afterSignIn.ts` (what happens next) and
`src/state/firstLaunch.ts` (`signedOutReset`, when the phone changes hands). The backend side is
`POST /api/auth/apple`, `/api/auth/google`, `/api/auth/logout` (root README, `APPLE_AUDIENCES`, `GOOGLE_CLIENT_IDS`).

Native modules, so a build made before them cannot run it and nothing here ships over the air:
`expo-apple-authentication` (entitlement `com.apple.developer.applesignin`, set by `ios.usesAppleSignIn` and the
plugin), `@react-native-google-signin/google-signin` (its plugin adds the iOS URL scheme) and `expo-crypto`.

| Variable | What it is |
| --- | --- |
| `EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID` | OAuth client of type **Web application**. The id token is issued for it on both platforms. Required for Google sign-in |
| `EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID` | OAuth client of type **iOS** (bundle id `io.truckbuddy.ios`). Required on iOS |
| `EXPO_PUBLIC_GOOGLE_IOS_URL_SCHEME` | The iOS client id reversed: `com.googleusercontent.apps.<the part before .apps.googleusercontent.com>`. Read at build time by `app.config.ts`; left out, it is worked out from the iOS client id. When both are set they must agree: a malformed value, or one that is not the reverse of the client id, stops `expo config` and the build with an error |

None of them is a secret. They are read at build time (`EXPO_PUBLIC_*` is baked into the bundle, the URL scheme
into Info.plist), so set them for every EAS environment the builds use (`npx eas-cli env:set production --name
EXPO_PUBLIC_GOOGLE_WEB_CLIENT_ID --value <id> --visibility plaintext`, the same for every other environment)
or in `eas.json` `env`, and in `.env.local` for a local run. Without them the config still evaluates (the Google
plugin is left out), the app starts, and the Google button answers "Google sign-in isn't set up in this build."
The same answer comes from an iOS binary that was built without the scheme and later got a bundle with the client
ids over the air: the app asks the system whether the scheme is registered (`googleUsable` in
`src/signin/available.ts`; `app.config.ts` lists the scheme under `LSApplicationQueriesSchemes` for that) before
it calls Google's SDK, which would otherwise crash the app.
Check: `EXPO_PUBLIC_GOOGLE_IOS_CLIENT_ID=<id> npx expo config --type introspect` shows the scheme under
`ios.infoPlist.CFBundleURLTypes` and `com.apple.developer.applesignin` under `ios.entitlements`.

One-time owner steps:

1. Apple Developer → Identifiers → `io.truckbuddy.ios` → enable **Sign in with Apple**. EAS regenerates the
   provisioning profile on the next build. (The key for revoking a sign-in on account deletion is a backend matter.)
2. Google Cloud console → OAuth consent screen (app name, support e-mail; scopes `email` and `profile` only), then
   three OAuth clients in the same project:
   - **iOS**, bundle id `io.truckbuddy.ios`;
   - **Android**, package `io.truckbuddy.app` plus the SHA-1 of the signing certificate: one client per keystore
     (`npx eas-cli credentials --platform android` prints the SHA-1 of each build profile's keystore; a build
     installed from Google Play is re-signed, so add the Play **App signing** SHA-1 too). The Android client id is
     never put in the app: Google matches the package and the SHA-1, and a mismatch shows as `DEVELOPER_ERROR`;
   - **Web application**, no redirect URIs needed.
3. Backend env, exactly:
   - `GOOGLE_CLIENT_IDS` = the **Web** client id + the **iOS** client id + **every Android** client id, comma
     separated. The debug keystore, the EAS upload key and Play App Signing each have their own Android client;
     leave one out and sign-in from that build is refused. Why all of them: an id token's `aud` is the Web
     client id on both platforms, and its `azp` is the client of the platform that asked (iOS or that Android
     keystore); the server checks both against this list.
   - `APPLE_AUDIENCES` = `io.truckbuddy.ios`.

What the sign-in screen says when it fails: no answer at all (offline, a timeout) "Couldn't sign in. Check the
connection and try again."; the server refused the provider's token (401; most often an id missing from the two
variables above) "Sign-in was refused. Try again, or use the other button."; 503 and other 5xx "Sign-in isn't
available right now. Try again in a minute."; 429 "Too many tries. Wait a few minutes."

Expo Go has neither provider (no Google module; Apple's token there is issued for Expo Go itself). In Expo Go and
in dev builds the sign-in screen has "I have an access code" (the emulator stand signs in with `TESTER_CODE` or
`ACCESS_CODE` from `backend/.env`). A staging or production build hides the link: hold the avatar on the sign-in
screen for 3 seconds and it appears (App Review and support; put this in the Review Notes with the tester code).

A phone that still holds the anonymous device token of build 1.0 is treated as signed out on 1.1.0: it gets the
sign-in screen, and the token and what the phone kept under it are cleared. An access-code sign-in stays.

Nothing about the real providers runs under jest: their native modules are mocked. Before a release, on a phone:
both buttons, a cancelled sheet, a second sign-in after a reinstall (the truck is back, "Welcome back" shows once),
sign-out and a sign-in as someone else.

## Running on a phone

- **Expo Go (quickest):** start the backend on your laptop, put the phone on the same Wi-Fi, then
  `EXPO_PUBLIC_BACKEND_URL=http://<laptop-ip>:8000 npx expo start` and scan the QR code with the
  iPhone camera. Maps, location, recording and playback work; `react-native-keyboard-controller`
  is swapped for plain views by `src/keyboard.tsx` because Expo Go does not ship its native module.
- **Dev build (JS reloads without rebuilding):** `npx eas-cli build --profile development --platform ios`,
  install from the link, then `npx expo start`. Rebuild only when native dependencies change.
- **Preview build (what App Review sees):** `npx eas-cli build --profile preview --platform ios`.
  `EXPO_PUBLIC_BACKEND_URL` comes from `eas.json` for preview and production.

## Android

### Google Maps key (required for a device build)

`react-native-maps` draws Google Maps on Android, and Google Maps renders a blank grey canvas
without an API key. The key lives outside git: `app.config.ts` reads it from
`GOOGLE_MAPS_ANDROID_KEY` and `expo prebuild` writes it into the manifest.

1. Google Cloud console: enable **Maps SDK for Android**, create an API key.
2. Restrict the key to Android apps: package `io.truckbuddy.app` plus the SHA-1 of the signing
   certificate. `npx eas-cli credentials --platform android` prints the SHA-1 of the keystore EAS
   signs with (one per build profile: the development profile and production use different keystores,
   add both fingerprints to the same key).
3. Store it as an EAS secret so every cloud build sees it (one call per environment the builds use):
   `npx eas-cli env:set development --name GOOGLE_MAPS_ANDROID_KEY --value <key> --visibility secret`
   and the same for `preview` and `production`. For a local `npx expo run:android`, export the
   variable in the shell (or put it in `.env.local`, which is git-ignored).
4. Check what prebuild will write: `GOOGLE_MAPS_ANDROID_KEY=x npx expo config --type prebuild`
   should show `android.config.googleMaps.apiKey`. `--type public` never shows it (the public
   manifest strips keys), so do not be alarmed when it is missing there.

### Builds

- **Dev build:** `npx eas-cli build --profile development --platform android`, install the APK from
  the link, then `npx expo start` and open the project from the dev client.
- **Preview / production:** same as iOS with `--platform android`; `eas.json` submits production to
  the Play **internal** track as a draft.

### Google Play closed test rule

A personal developer account created after November 2023 cannot publish to production until a
closed test has run with **at least 12 testers opted in, continuously, for 14 days**. Start the
closed track early: upload a preview build, add the testers by e-mail or Google Group, make sure
all 12 have installed it, and only then apply for production access in Play Console.

### Device checklist (before each Android build goes to testers)

1. Map renders tiles and the user arrow (a grey grid means the Maps key or its restriction is wrong).
2. Location prompt appears once; denying it shows the "Location off" tag, and tapping it reopens Settings.
3. Tap the copilot, say a stop: the recording uploads, the transcript shows, the reply plays on the loudspeaker.
4. "Navigate" opens Google Maps in driving mode; Waze opens the app when installed, otherwise the browser.
5. Truck profile: Motive key saves ("Connected"), survives an app restart, and is gone after Remove.
6. Hardware back button: leaves the sheet / screen you are on, and only exits the app from the map.

### Audit: iOS-only code paths (2026-10-27)

Every `Platform.OS === "ios"` branch in `src/` and `app/` has an Android path. Nothing was broken;
these are the Android-specific gaps still open:

- `app.config.ts` lists `comgooglemaps` / `waze` under `ios.infoPlist.LSApplicationQueriesSchemes`
  only. No code opens those apps any more: the only map link is Street View, an `https://` URL opened
  with `Linking.openURL` (`src/utils/navLinks.ts`), and nothing calls `canOpenURL`, so Android needs
  no `<queries>` block. If a `canOpenURL` check or an app-scheme link comes back, add one through a
  config plugin.
- Location services switched off (not just denied): `useDriverLocation` catches the error and stays
  on the last known fix. Android could call `Location.enableNetworkProviderAsync()` to show the
  system "turn on location" dialog; iOS has no such API, so the hook does not try.
- No `BackHandler` anywhere: the hardware back button is handled by expo-router only, so on the map
  screen it backgrounds the app even when the options sheet or the providers list is open.
- `ParkingSatMap.native.tsx` uses `mapType="none"` on Android (Esri tiles only) and
  `shouldReplaceMapContent` on iOS; the two look the same but have not been compared on a device.
- `android.adaptiveIcon` has no `monochromeImage`, so Android 13+ themed icons fall back to the
  default tinted glyph.
- The wake word (`useWakeWord`) is off on both platforms; the `Platform.OS !== "ios" || appActive`
  guard in `app/index.tsx` is dormant until a detector ships.

## Offline: the saved route and the MapLibre map

Two layers, independent of each other:

- **The route survives no signal and restarts (always on).** The active route is written to
  AsyncStorage (`active_route_v1`) on every change and restored on launch when younger than 12 h
  ("Route restored: …" banner, Resume / Dismiss). Turn-by-turn, advisories and the speed shield
  read only the stored route and GPS. In a dead zone the copilot says so once, points back at the
  loaded line instead of rerouting ("The route is 0.3 miles to the west"), refuses voice turns
  with "No signal right now", and speaks through the phone's own voice (`expo-speech`) because
  the cloud voice needs the server. Works in Expo Go.
- **Map tiles offline (MapLibre build only).** With react-native-maps (the default) the base map
  is Apple Maps / Google Maps and goes blank without signal. The MapLibre provider draws
  OpenFreeMap vector tiles and can save a corridor pack per route.

### Building with MapLibre

```sh
EXPO_PUBLIC_MAP_PROVIDER=maplibre npx eas-cli build --profile development --platform ios
EXPO_PUBLIC_MAP_PROVIDER=maplibre npx expo start   # the same variable at bundle time
```

The variable does two things: `app.config.ts` adds the `@maplibre/maplibre-react-native` config
plugin (native SDK on both platforms), and `src/map/provider.ts` makes the JS pick
`MapLayerLibre` instead of `MapLayerApple`. Without it nothing changes: the plugin is not added,
the library is never required (`src/map/libre.ts` loads it lazily inside a try, like the keyboard
controller), Expo Go keeps working. Expo Go cannot run MapLibre at all: with the variable set in
Expo Go the switch falls back to Apple Maps and the offline-map UI stays hidden. Set the variable
in `eas.json` `env` for a profile to make it stick.

`ParkingSatMap` stays on react-native-maps whatever the provider: its Esri satellite tiles are
online-only.

### Attribution (required)

OpenFreeMap is free and needs no key, on one condition: the attribution must stay visible.
`MapLayerLibre` renders "© OpenFreeMap © OpenMapTiles © OpenStreetMap contributors" in the map's
corner and keeps MapLibre's own attribution button on; do not remove either. The dark theme uses
`https://tiles.openfreemap.org/styles/dark`, light uses `.../liberty`.

### Pack sizes

A pack is the route's bounding box plus 3 km, zoom 6 to 14, named `route-<hash of the geometry>`
(`src/map/bounds.ts`). The card shows an estimate before the download (tile count × ~25 KB):

| Route | Bbox | Estimate |
| --- | --- | --- |
| Sacramento → Stockton (~75 km, north-south) | ~0.8° × 0.3° | ~15 MB |
| Sacramento → Reno (~210 km, diagonal) | ~1.7° × 1.0° | ~90 MB |
| Sacramento → Los Angeles (~600 km) | ~3.5° × 4° | ~800 MB, do not |

The last three packs are kept (the oldest is deleted before a new one downloads); there is no
state-wide pack on purpose. Packs are listed with size and a delete button under Truck profile ›
Offline maps. The tile count limit is raised to 40 000 per device for the download. The real size
on disk comes back from MapLibre once the pack is complete ("Saved · 84 MB").

Needs a device with the dev build to verify: tile rendering, camera follow/fit, marker taps, the
download itself and offline rendering from a pack. None of that runs under jest.

## Wake word

Not in this version. Picovoice closed its free tier on 2026-06-30, so Porcupine was removed;
the copilot starts with a tap. `src/hooks/useWakeWord.ts` keeps the hook's shape and reports
"unsupported", which also hides the Hands-free switch in the truck profile.
Candidate for the next version: `react-native-voice-activator` (MIT, sherpa-onnx on device,
any phrase without training or a key). It must be tested on a real phone before it ships.

- **Dialog after a route is set:** once the copilot answers "Route is set", the conversation ends and
  the mic turns off. Follow-ups like "not that one" or "something closer" need a new tap.

See the root README for running the app and the backend.
