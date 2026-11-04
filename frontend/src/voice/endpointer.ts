// End of the driver's turn from the recorder's level meter, as a pure function of (dB, time) samples.
// No React, no expo: useVoice.listen() feeds it while recording and stops on its decision; the tests feed it
// level profiles (synthetic ones and, later, profiles recorded in the cab).
//
// Why it changed (2026-11-04, «TruckBuddy — Голос по кнопке, как рация»): the old loop measured the cab noise
// in the first 400 ms only (speech at the chime pushed the threshold above the voice), wanted 8 dB over the
// noise for 300 ms ("go" and "yes" never counted, a dash-mounted phone on the highway never counted), and threw
// the recording away after 7 s without counted speech, words and all.

/** Speech = this far above the cab noise (rolling minimum). */
export const SPEECH_OVER_NOISE_DB = 6;
/** Loud time inside the last LOUD_WINDOW_MS that makes it speech ("go" can dip in the middle). */
export const SPEECH_MIN_MS = 200;
export const LOUD_WINDOW_MS = 400;
/** The cab noise is the quietest level of the last NOISE_WINDOW_MS (gaps between words are quieter than words). */
export const NOISE_WINDOW_MS = 2000;
export const END_SILENCE_MS = 1200;
export const MAX_MS = 30000;
export const WARN_BEFORE_MAX_MS = 3000;
export const START_TIMEOUT_MS = 7000;
/** No counted speech by the timeout, but something this far over the noise for MAYBE_MIN_MS: send it anyway. */
export const MAYBE_OVER_NOISE_DB = 4;
export const MAYBE_MIN_MS = 100;

/** wait: keep recording. send: the turn is over. maybe: no clear speech, but something; the server decides.
 *  silent: nothing worth sending. warn: still recording, the cap is close (play a tick once). */
export type Decision = "wait" | "warn" | "send" | "maybe" | "silent";
export type EndReason = "silence" | "cap" | "maybe" | "silent";

export type EndpointerOptions = { endSilenceMs?: number; maxMs?: number; startTimeoutMs?: number };

export type EndpointerStats = { noiseDb: number; voiceDb: number | null; peakDb: number; spoke: boolean; reason: EndReason | null; lastT: number };

export type Endpointer = {
  /** One meter sample: level in dBFS, `t` in ms since the recording started (non-decreasing). */
  push: (db: number, t: number) => Decision;
  stats: () => EndpointerStats;
};

export function createEndpointer(opts: EndpointerOptions = {}): Endpointer {
  const endSilenceMs = opts.endSilenceMs ?? END_SILENCE_MS;
  const maxMs = opts.maxMs ?? MAX_MS;
  const startTimeoutMs = opts.startTimeoutMs ?? START_TIMEOUT_MS;
  const history: { t: number; db: number }[] = []; // the last NOISE_WINDOW_MS of samples
  let prevT = 0;
  let loud: { t: number; dt: number }[] = []; // loud samples with their duration, last LOUD_WINDOW_MS
  let spoke = false;
  let quietSince: number | null = null;
  let voiceSum = 0;
  let voiceN = 0;
  let peak = -160;
  let maybeMs = 0;
  let warned = false;
  let noise = 0;
  let reason: EndReason | null = null;

  const end = (r: EndReason, d: Decision): Decision => {
    reason = r;
    return d;
  };

  const push = (db: number, t: number): Decision => {
    if (reason) return reason === "silent" ? "silent" : reason === "maybe" ? "maybe" : "send";
    const dt = Math.max(0, t - prevT);
    prevT = t;
    if (Number.isFinite(db)) {
      peak = Math.max(peak, db);
      history.push({ t, db });
      while (history.length && history[0].t < t - NOISE_WINDOW_MS) history.shift();
      // The noise floor: the quietest recent sample. When the driver talks from the first frame it sits on the
      // voice for a moment, until the first gap between words pulls it down; the recording runs on meanwhile.
      noise = Math.min(...history.map((h) => h.db));
      if (db > noise + SPEECH_OVER_NOISE_DB) {
        loud.push({ t, dt });
        loud = loud.filter((l) => l.t > t - LOUD_WINDOW_MS);
        if (loud.reduce((a, l) => a + l.dt, 0) >= SPEECH_MIN_MS) spoke = true;
        if (spoke) {
          voiceSum += db;
          voiceN++;
        }
        quietSince = null;
      } else {
        loud = loud.filter((l) => l.t > t - LOUD_WINDOW_MS);
        // Anything under the speech level is a pause: a band in between would keep a noisy cab (radio, wind)
        // recording until the cap.
        if (spoke) {
          quietSince = quietSince ?? t;
          if (t - quietSince >= endSilenceMs) return end("silence", "send");
        }
      }
      if (!spoke && db > noise + MAYBE_OVER_NOISE_DB) maybeMs += dt;
    }
    if (!spoke && t >= startTimeoutMs) return maybeMs >= MAYBE_MIN_MS ? end("maybe", "maybe") : end("silent", "silent");
    if (t >= maxMs) return end("cap", "send");
    if (!warned && t >= maxMs - WARN_BEFORE_MAX_MS) {
      warned = true;
      return "warn";
    }
    return "wait";
  };

  return {
    push,
    stats: () => ({ noiseDb: noise, voiceDb: voiceN ? voiceSum / voiceN : null, peakDb: peak, spoke, reason, lastT: prevT }),
  };
}
