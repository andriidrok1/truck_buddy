// On-device speech (expo-speech) for the lines that must be heard without the network: maneuver
// calls, "Lost signal", "the route is 0.4 miles to the west". The cloud voice (/api/speak) is the
// normal path; this is the fallback the speech queue takes when the phone is offline or that
// call fails. Robotic next to the cloud voice, which is the point: the driver hears the difference.

import * as Speech from "expo-speech";

const RATE = 0.95;

/** Speaks `text` and resolves when it is done (or after `maxMs`, or at once if the engine refuses). */
export function speakLocal(text: string, maxMs = 15000): Promise<void> {
  return new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve();
    };
    const timer = setTimeout(() => {
      Speech.stop().catch(() => {});
      finish();
    }, maxMs);
    try {
      Speech.speak(text.slice(0, Speech.maxSpeechInputLength || 4000), {
        language: "en-US",
        rate: RATE,
        onDone: finish,
        onStopped: finish,
        onError: finish,
      });
    } catch (e) {
      console.log(`[speech] local TTS failed: ${String(e)}`);
      finish();
    }
  });
}

export const stopLocal = () => Speech.stop().catch(() => {});
