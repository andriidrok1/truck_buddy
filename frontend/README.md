# TruckBuddy app

## Running on a phone

- **Expo Go (quickest):** start the backend on your laptop, put the phone on the same Wi-Fi, then
  `EXPO_PUBLIC_BACKEND_URL=http://<laptop-ip>:8000 npx expo start` and scan the QR code with the
  iPhone camera. Maps, location, recording and playback work; `react-native-keyboard-controller`
  is swapped for plain views by `src/keyboard.tsx` because Expo Go does not ship its native module.
- **Dev build (JS reloads without rebuilding):** `npx eas-cli build --profile development --platform ios`,
  install from the link, then `npx expo start`. Rebuild only when native dependencies change.
- **Preview build (what App Review sees):** `npx eas-cli build --profile preview --platform ios`.
  `EXPO_PUBLIC_BACKEND_URL` comes from `eas.json` for preview and production.

## Wake word

Not in this version. Picovoice closed its free tier on 2026-06-30, so Porcupine was removed;
the copilot starts with a tap. `src/hooks/useWakeWord.ts` keeps the hook's shape and reports
"unsupported", which also hides the Hands-free switch in the truck profile.
Candidate for the next version: `react-native-voice-activator` (MIT, sherpa-onnx on device,
any phrase without training or a key). It must be tested on a real phone before it ships.

- **Dialog after a route is set:** once the copilot answers "Route is set", the conversation ends and
  the mic turns off. Follow-ups like "not that one" or "something closer" need a new tap.

See the root README for running the app and the backend.
