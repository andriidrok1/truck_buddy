// Web preview: no wake word (Porcupine is native-only). Tap to talk keeps working.
const noop = () => Promise.resolve();

export const WAKE_SUPPORTED = false;

export function useWakeWord(_onWake: () => void) {
  return { active: false, error: null as string | null, phrase: "Hey Buddy", start: noop, stop: noop, shutdown: noop, supported: false };
}
