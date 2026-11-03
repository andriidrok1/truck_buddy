# Stand on the dev client (replaces Expo Go)

Why: Expo Go has a fixed set of native modules. MapLibre, background location (foreground service), keep-awake
and background audio are not in it, so the stand could never exercise them and the stand binary differed
from what drivers get. The dev client is our own build of the app with the same Metro connection, hot
reload and dev menu.

## One-time, owner (needs the Expo account)

```
cd frontend
npx eas-cli login
npx eas-cli init                      # writes the project id; put it into EAS env as EXPO_PUBLIC_EAS_PROJECT_ID
npx eas-cli build --profile development --platform android
```

The build page gives an APK link. Download it to `/data/Android/truckbuddy-dev.apk` (any path works, up.sh
takes `DEV_CLIENT_APK`).

Optional secrets for the profiles: `GOOGLE_MAPS_ANDROID_KEY` (already used), `EXPO_PUBLIC_SENTRY_DSN`,
`SENTRY_ORG`, `SENTRY_PROJECT`, `SENTRY_AUTH_TOKEN` (source maps), `EXPO_TOKEN` (CI builds).

## Every stand start (what `~/.claude/skills/test-app/scripts/up.sh` should do instead of Expo Go)

```
adb install -r "$DEV_CLIENT_APK"                       # only when the APK changed
adb reverse tcp:8081 tcp:8081; adb reverse tcp:8001 tcp:8001
cd frontend && EXPO_PUBLIC_BACKEND_URL=http://10.0.2.2:8001 npx expo start --dev-client --localhost --port 8081
adb shell am start -a android.intent.action.VIEW -d "truckbuddy://expo-development-client/?url=http%3A%2F%2F127.0.0.1%3A8081"
```

The scheme is `truckbuddy` (app.config.ts). The dev client opens the Metro bundle from that URL; a reload is
the usual dev menu (`adb shell input keyevent 82`). `--go` and the `exp://` deep link go away.

MapLibre on the stand: build the dev client with `EXPO_PUBLIC_MAP_PROVIDER=maplibre` in the profile env to
include the native SDK; the JS side picks the map from the same variable.

## Phones

Same APK (Android) or a TestFlight/ad hoc build (iOS) from the `development` profile; drivers' builds come
from `staging` (internal) and `production` (store). JS fixes go out with `eas update --branch staging` /
`--branch production` and arrive on the next app open.
