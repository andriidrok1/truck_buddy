# TruckBuddy app

## Hands-free wake word ("Hey Buddy")

The copilot can start a conversation when the driver says the wake word. Detection runs
on the phone (Picovoice Porcupine); nothing is recorded or sent before the wake word fires.

- **Works only in a dev/production build.** Expo Go and the web preview keep "Tap to talk".
- **Access key:** create a free account at https://console.picovoice.ai (Free tier), copy the
  AccessKey and set `EXPO_PUBLIC_PICOVOICE_ACCESS_KEY` in `frontend/.env`. No key = feature off.
- **Custom keyword:** in Picovoice Console train "Hey Buddy" for Android and iOS, download the
  files and save them as `assets/wakeword/hey_buddy_android.ppn` and `assets/wakeword/hey_buddy_ios.ppn`.
  Without them the app uses the built-in keyword "Jarvis" (the caption shows the real phrase).
- **Dev build:** `npx expo prebuild --clean` then `npx expo run:android` / `npx expo run:ios`
 . `plugins/withWakeWord.js` adds the Android
  microphone foreground service and bundles the `.ppn` files.
- Toggle: Truck profile → "Hands-free" (on by default).
- Android 13+: the app asks for notifications before starting the listening service. If denied,
  the wake word works only while the app is on screen.
- Wake word heard while the app is in the background: the chime plays and the copilot starts
  listening as soon as the driver opens the app (within 60 s). Recording never starts from the background.
- **Dialog after a route is set:** once the copilot answers "Route is set", the conversation ends and
  the mic turns off. Follow-ups like "not that one" or "something closer" need a new tap or wake word.

See the root README for running the app and the backend.
