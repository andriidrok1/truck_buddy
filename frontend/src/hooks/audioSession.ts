import { setIsAudioActiveAsync } from "expo-audio";
import { Platform } from "react-native";

// iOS keeps other audio ducked while the app's session is active (PLAYBACK_MODE is "duckOthers"), and expo-audio
// turns the session off only when a clip plays to its end. A reply cut short (Go tapped, a new answer, the
// playback timer) left the radio at half volume until some later clip happened to finish (review 2026-11-02).
// release() turns the session off; acquire() turns it on again before the next sound or recording. They are one
// chain: setIsAudioActiveAsync(false) also pauses every player, so a release that lands after the next clip
// started would cut it. Android gives audio focus back when the player is removed, so this is iOS only.

let releasing: Promise<void> | null = null;

export function releaseAudioSession(): void {
  if (Platform.OS !== "ios") return;
  const prev = releasing ?? Promise.resolve();
  releasing = prev.then(() => setIsAudioActiveAsync(false)).catch(() => {});
}

export async function acquireAudioSession(): Promise<void> {
  if (Platform.OS !== "ios" || !releasing) return;
  const pending = releasing;
  await pending;
  if (releasing === pending) releasing = null; // a release queued meanwhile stays queued
  await setIsAudioActiveAsync(true).catch(() => {});
}
