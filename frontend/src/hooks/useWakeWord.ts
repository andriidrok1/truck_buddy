// Wake word ("Hey Buddy") is not shipped in this version: Picovoice closed its free tier on
// 2026-06-30 and the replacement (an on-device open-source detector) has not been tested on a
// phone yet. The hook keeps the same shape so index.tsx needs no changes; it always reports
// "unsupported", which hides the Hands-free switch and leaves "Tap to talk".
// Candidate for 1.1: react-native-voice-activator (MIT, sherpa-onnx, any phrase, no key).
const noop = () => Promise.resolve();

export const WAKE_SUPPORTED = false;

export function useWakeWord(_onWake: () => void) {
  return { active: false, error: null as string | null, phrase: "Hey Buddy", start: noop, stop: noop, shutdown: noop, supported: false };
}
