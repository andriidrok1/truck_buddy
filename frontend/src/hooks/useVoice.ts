import {
  createAudioPlayer,
  getRecordingPermissionsAsync,
  RecordingPresets,
  requestRecordingPermissionsAsync,
  setAudioModeAsync,
  useAudioRecorder,
  type AudioPlayer,
} from "expo-audio";
import * as Haptics from "expo-haptics";
import { useCallback, useEffect, useRef, useState } from "react";
import { Platform } from "react-native";

export type MicPermission = "granted" | "ask" | "blocked";

const SPEECH_DB = -38; // lower bound for "the driver is talking" (quiet cab)
const SILENCE_DB = -45; // lower bound for "pause"
const SPEECH_OVER_FLOOR = 10; // dB above the calibrated noise floor
const SILENCE_OVER_FLOOR = 4;
const CALIBRATE_MS = 400; // the first part of each listen measures the cab noise
const SUSTAIN_MS = 300; // speech only counts after this long above the threshold
const DROP_FROM_VOICE_DB = 14; // this far under the driver's average voice = a pause, whatever the cab noise
const POLL_MS = 100;
const CHIME = require("../../assets/sounds/chime.mp3");

// iOS: shouldRouteThroughEarpiece false = .defaultToSpeaker whenever the session is playAndRecord.
// The recorder's prepare reuses the options of the last mode, so it must follow RECORD_MODE.
const RECORD_MODE = { allowsRecording: true, playsInSilentMode: true, shouldRouteThroughEarpiece: false };
// Replies play in a pure playback session (never play-and-record, which iOS sends to the earpiece).
const PLAYBACK_MODE = { allowsRecording: false, playsInSilentMode: true, shouldRouteThroughEarpiece: false };

type ListenOptions = { maxMs?: number; silenceMs?: number; startTimeoutMs?: number; chime?: boolean };

// 16 kHz mono AAC: Whisper resamples to 16 kHz anyway, and the upload is ~4x smaller.
const SPEECH_PRESET = {
  ...RecordingPresets.HIGH_QUALITY,
  sampleRate: 16000,
  numberOfChannels: 1,
  bitRate: 32000,
  isMeteringEnabled: true,
  web: { ...RecordingPresets.HIGH_QUALITY.web, bitsPerSecond: 32000 },
};

// Microphone recording + reply playback (expo-audio).
export function useVoice() {
  const recorder = useAudioRecorder(SPEECH_PRESET);
  const player = useRef<AudioPlayer | null>(null);
  const playDone = useRef<(() => void) | null>(null);
  const subs = useRef(new Map<AudioPlayer, { remove: () => void }>());
  const startedAt = useRef(new Map<AudioPlayer, number>());
  const lastFinishAt = useRef(0);
  const listenToken = useRef(0);
  const finishNow = useRef(false);
  const finishListening = useCallback(() => {
    finishNow.current = true;
  }, []);
  const [speaking, setSpeaking] = useState(false);

  const micPermission = useCallback(async (): Promise<MicPermission> => {
    const p = await getRecordingPermissionsAsync();
    if (p.granted) return "granted";
    return p.canAskAgain ? "ask" : "blocked";
  }, []);

  const requestMic = useCallback(async (): Promise<MicPermission> => {
    const p = await requestRecordingPermissionsAsync();
    if (p.granted) return "granted";
    return p.canAskAgain ? "ask" : "blocked";
  }, []);

  // Recorder prepared while idle, so record() starts the moment the driver taps (native only:
  // on web preparing opens the mic). Off while the wake word owns the mic (Android can't share it).
  // Never while a reply plays: `audioGen` changes on every play / record, and an idle prepare
  // that sees a newer generation after any await gives up.
  const prepared = useRef(false);
  const prewarm = useRef(false);
  const audioGen = useRef(0);
  const prepareIdle = useCallback(async () => {
    if (Platform.OS === "web" || !prewarm.current || prepared.current || recorder.isRecording || player.current) return;
    const gen = audioGen.current;
    const stale = () => gen !== audioGen.current || player.current !== null || recorder.isRecording;
    try {
      const p = await getRecordingPermissionsAsync();
      if (!p.granted || stale()) return;
      await setAudioModeAsync(RECORD_MODE);
      if (stale()) return;
      await recorder.prepareToRecordAsync();
      if (stale()) return;
      prepared.current = true;
    } catch (e) {
      console.log(`[voice] idle prepare failed: ${String(e)}`);
    }
  }, [recorder]);

  const stopSpeaking = useCallback(() => {
    const p = player.current;
    if (p) {
      subs.current.get(p)?.remove();
      subs.current.delete(p);
      startedAt.current.delete(p);
      p.remove();
    }
    player.current = null;
    setSpeaking(false);
    playDone.current?.();
    playDone.current = null;
    // A reply just finished (or was cut): get the mic ready for the next tap.
    if (p) prepareIdle();
  }, [prepareIdle]);
  const setPrewarm = useCallback(
    (on: boolean) => {
      prewarm.current = on;
      if (on) prepareIdle();
    },
    [prepareIdle],
  );

  const startRecording = useCallback(async () => {
    stopSpeaking();
    audioGen.current++;
    await setAudioModeAsync(RECORD_MODE);
    if (!prepared.current) await recorder.prepareToRecordAsync();
    prepared.current = false;
    recorder.record();
  }, [recorder, stopSpeaking]);

  // Returns the local file uri, or null if nothing was recorded.
  const stopRecording = useCallback(async (): Promise<string | null> => {
    if (!recorder.isRecording) return recorder.uri ?? null;
    await recorder.stop();
    await setAudioModeAsync(PLAYBACK_MODE);
    // No re-prepare here: the reply comes next and must play in the playback session.
    // The recorder is prepared again once it finishes (stopSpeaking) or when idle.
    return recorder.uri ?? null;
  }, [recorder]);

  const cancelRecording = useCallback(async () => {
    listenToken.current++;
    if (recorder.isRecording) await recorder.stop();
    await setAudioModeAsync(PLAYBACK_MODE);
    prepareIdle(); // idle again; any reply that starts meanwhile cancels this (audioGen)
  }, [recorder, prepareIdle]);

  /**
   * Hands-free answer: records until the driver stops talking.
   * The first ~400 ms calibrate the noise floor (speech = floor + 10 dB, pause = floor + 4 dB,
   * never below the fixed quiet-cab values); speech counts after 300 ms above the threshold.
   * Resolves the file uri, "silent" if nobody spoke, or "cancelled".
   */
  const listen = useCallback(
    async ({ maxMs = 15000, silenceMs = 1100, startTimeoutMs = 7000, chime = true }: ListenOptions = {}) => {
      const token = ++listenToken.current;
      finishNow.current = false;
      await startRecording();
      if (chime) {
        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
        const c = createAudioPlayer(CHIME);
        c.play();
        setTimeout(() => c.remove(), 1500);
      }
      const t0 = Date.now();
      let spoke = false;
      let quietSince = 0;
      let loudSince = 0;
      let voiceSum = 0; // the driver's speech level this turn (average)
      let voiceN = 0;
      let hasMeter = false;
      const calib: number[] = [];
      let speechDb = SPEECH_DB;
      let silenceDb = SILENCE_DB;
      let calibrated = false;
      let peak = -160;
      let sum = 0;
      let n = 0;
      let ended = "max";
      while (listenToken.current === token) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        if (listenToken.current !== token) break;
        if (finishNow.current) {
          spoke = true;
          ended = "tap";
          break;
        }
        const db = recorder.getStatus().metering;
        const now = Date.now();
        if (typeof db === "number" && Number.isFinite(db)) {
          hasMeter = true;
          peak = Math.max(peak, db);
          sum += db;
          n++;
          if (!calibrated) {
            calib.push(db);
            if (now - t0 < CALIBRATE_MS) continue;
            // Quietest sample = the cab noise (the chime or an early word only raise the others).
            const floor = Math.min(...calib);
            speechDb = Math.max(SPEECH_DB, floor + SPEECH_OVER_FLOOR);
            silenceDb = Math.max(SILENCE_DB, floor + SILENCE_OVER_FLOOR);
            calibrated = true;
            console.log(`[voice] calibrated floor=${floor.toFixed(1)} speech>${speechDb.toFixed(1)} silence<${silenceDb.toFixed(1)} samples=${calib.map((v) => v.toFixed(0)).join(",")}`);
          }
          if (db > speechDb) {
            loudSince = loudSince || now;
            if (now - loudSince >= SUSTAIN_MS) spoke = true;
            if (spoke) {
              voiceSum += db;
              voiceN++;
            }
            quietSince = 0;
          } else {
            loudSince = 0;
            // Pause = below the floor-based level, or well under the driver's average voice (loud cab
            // noise that never drops to the floor must not keep the mic open for 15 s).
            const voiceDb = voiceN ? voiceSum / voiceN : -160;
            if (spoke && (db < silenceDb || db < voiceDb - DROP_FROM_VOICE_DB)) {
              quietSince = quietSince || now;
              if (now - quietSince >= silenceMs) {
                ended = "silence";
                break;
              }
            }
          }
        }
        if (!spoke && hasMeter && now - t0 > startTimeoutMs) {
          console.log(`[voice] no speech: peak=${peak.toFixed(1)} avg=${n ? (sum / n).toFixed(1) : "-"} speech>${speechDb.toFixed(1)}`);
          await cancelRecording();
          return "silent" as const;
        }
        // No level meter on this device: fixed answer window.
        if (!hasMeter && now - t0 > 7000) {
          spoke = true;
          ended = "no-meter";
          break;
        }
        if (now - t0 > maxMs) break;
      }
      if (listenToken.current !== token) return "cancelled" as const;
      console.log(`[voice] turn end=${ended} ms=${Date.now() - t0} peak=${peak.toFixed(1)} voice=${voiceN ? (voiceSum / voiceN).toFixed(1) : "-"} avg=${n ? (sum / n).toFixed(1) : "-"} speech>${speechDb.toFixed(1)} silence<${silenceDb.toFixed(1)} silenceMs=${silenceMs}`);
      return (await stopRecording()) ?? ("silent" as const);
    },
    [recorder, startRecording, stopRecording, cancelRecording],
  );

  const play = useCallback(
    (source: string | AudioPlayer) => {
      stopSpeaking();
      audioGen.current++;
      const p = typeof source === "string" ? createAudioPlayer({ uri: source }) : source;
      player.current = p;
      setSpeaking(true);
      const sub = p.addListener("playbackStatusUpdate", (s) => {
        if (player.current !== p) return;
        if (s.playing && startedAt.current.get(p) === undefined) {
          const now = Date.now();
          startedAt.current.set(p, now);
          console.log(`[voice] play start t=${now}${lastFinishAt.current ? ` gap=${now - lastFinishAt.current}ms` : ""}`);
        }
        if (s.didJustFinish) {
          lastFinishAt.current = Date.now();
          console.log(`[voice] didJustFinish t=${lastFinishAt.current} dur=${Math.round((s.duration || 0) * 1000)}ms`);
          stopSpeaking();
        }
      });
      subs.current.set(p, sub);
      if (Platform.OS === "web") {
        p.play();
        return;
      }
      // Native: the playback session must be in place before the first sample (loudspeaker on iOS).
      // That leaves no prepared recorder behind, so the next tap prepares it again.
      prepared.current = false;
      setAudioModeAsync(PLAYBACK_MODE)
        .catch((e) => console.log(`[voice] playback mode failed: ${String(e)}`))
        .finally(() => {
          if (player.current === p) p.play();
        });
    },
    [stopSpeaking],
  );

  /** Creates a paused player so the file is already buffered when it's time to play it. */
  const preload = useCallback((uri: string) => createAudioPlayer({ uri }), []);

  /**
   * Plays and resolves when finished. `maxMs` only guards playback that never starts
   * (e.g. autoplay blocked). Once audio really plays (not buffering), the safety timer is
   * the remaining duration + duration*0.5 + 2 s.
   */
  const playAndWait = useCallback(
    (source: string | AudioPlayer, maxMs: number) =>
      new Promise<void>((resolve) => {
        play(source);
        const p = player.current;
        let timer = setTimeout(() => stopSpeaking(), Math.max(maxMs, 6000));
        let armed = false;
        const sub = p?.addListener("playbackStatusUpdate", (s) => {
          if (armed || player.current !== p || !s.playing || s.isBuffering) return;
          armed = true;
          clearTimeout(timer);
          const dur = s.duration > 0 ? s.duration * 1000 : 0;
          const left = dur ? dur - (s.currentTime || 0) * 1000 + dur * 0.5 + 2000 : 60000;
          timer = setTimeout(() => player.current === p && stopSpeaking(), left);
        });
        playDone.current = () => {
          clearTimeout(timer);
          sub?.remove();
          resolve();
        };
      }),
    [play, stopSpeaking],
  );

  useEffect(
    () => () => {
      listenToken.current++;
      player.current?.remove();
    },
    [],
  );

  return {
    micPermission,
    requestMic,
    startRecording,
    stopRecording,
    cancelRecording,
    listen,
    setPrewarm,
    finishListening,
    play,
    preload,
    playAndWait,
    stopSpeaking,
    speaking,
  };
}
