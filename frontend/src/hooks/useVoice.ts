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

import { acquireAudioSession, releaseAudioSession } from "./audioSession";
import { useCallback, useEffect, useRef, useState } from "react";
import { Platform } from "react-native";
import { useSharedValue, type SharedValue } from "react-native-reanimated";

import { createEndpointer, MAX_MS } from "../voice/endpointer";

export type MicPermission = "granted" | "ask" | "blocked";

// End of the turn lives in src/voice/endpointer.ts (pure, tested); this hook only polls the meter and acts.
const POLL_MS = 50;
const NO_METER_MS = 10000; // a device without a level meter: fixed answer window
const CHIME = require("../../assets/sounds/chime.mp3");
const CUES = {
  sent: require("../../assets/sounds/sent.mp3"),
  miss: require("../../assets/sounds/miss.mp3"),
  tick: require("../../assets/sounds/tick.mp3"),
} as const;
export type Cue = keyof typeof CUES;
/** A recording with no clear speech but something in it: the server decides (api.transcribe `maybe`). */
export type MaybeRecording = { uri: string; maybe: true };

// iOS: shouldRouteThroughEarpiece false = .defaultToSpeaker whenever the session is playAndRecord.
// The recorder's prepare reuses the options of the last mode, so it must follow RECORD_MODE.
const RECORD_MODE = { allowsRecording: true, playsInSilentMode: true, shouldRouteThroughEarpiece: false };
// Replies play in a pure playback session (never play-and-record, which iOS sends to the earpiece).
// It keeps playing with the screen off or another app in front (turn calls while the phone is locked), and lowers
// the radio or music under it instead of stopping it (plan A3, 2026-11-02).
const PLAYBACK_MODE = {
  allowsRecording: false,
  playsInSilentMode: true,
  shouldRouteThroughEarpiece: false,
  shouldPlayInBackground: true,
  interruptionMode: "duckOthers",
} as const;

type ListenOptions = { maxMs?: number; silenceMs?: number; startTimeoutMs?: number; chime?: boolean };

/** A short sound and a haptic, fire and forget. "sent" and "miss" play once the mic is off; "start" and "tick"
 *  play while recording (short and quiet: the recogniser ignores them). */
function playCue(cue: Cue | "start") {
  const c = createAudioPlayer(cue === "start" ? CHIME : CUES[cue]);
  if (cue === "start") c.volume = 0.6;
  c.play();
  setTimeout(() => c.remove(), 1500);
  if (cue === "start") return; // the tap itself already buzzed (CopilotSheet), the instant the finger landed
  const h = cue === "miss" ? Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning) : Haptics.impactAsync(cue === "tick" ? Haptics.ImpactFeedbackStyle.Light : Haptics.ImpactFeedbackStyle.Medium);
  h.catch(() => {});
}

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
  /** The mic permission as last seen (micPermission / requestMic): once granted, a tap skips the round trip. */
  const micGranted = useRef(false);

  const micPermission = useCallback(async (): Promise<MicPermission> => {
    const p = await getRecordingPermissionsAsync();
    micGranted.current = p.granted;
    if (p.granted) return "granted";
    return p.canAskAgain ? "ask" : "blocked";
  }, []);
  /** Granted as far as this session knows: the tap skips the permission round trip. */
  const micKnownGranted = useCallback(() => micGranted.current, []);

  const requestMic = useCallback(async (): Promise<MicPermission> => {
    const p = await requestRecordingPermissionsAsync();
    micGranted.current = p.granted;
    if (p.granted) return "granted";
    return p.canAskAgain ? "ask" : "blocked";
  }, []);

  // Recorder prepared while idle, so record() starts the moment the driver taps (native only:
  // on web preparing opens the mic). Off while the wake word owns the mic (Android can't share it).
  // Never while a reply plays: `audioGen` changes on every play / record, and an idle prepare
  // that sees a newer generation after any await gives up.
  const prepared = useRef(false);
  const prewarm = useRef(false);
  /**
   * Prepare the recorder unless it already is. Android keeps a MediaRecorder prepared across a reply's
   * playback and throws "already been prepared" on the next prepare, while the JS flag had been cleared
   * by play(): that is how the mic died after the first spoken answer (QA 2026-10-28). The native status
   * (`canRecord` = prepared) is the truth there; iOS tolerates a second prepare, so it keeps its path.
   */
  const ensurePrepared = useCallback(async () => {
    if (prepared.current) return;
    if (Platform.OS === "android") {
      let native = false;
      try {
        native = !!recorder.getStatus().canRecord;
      } catch {
        native = false;
      }
      if (native) {
        prepared.current = true;
        return;
      }
    }
    try {
      await recorder.prepareToRecordAsync();
    } catch (e) {
      if (!/already been prepared/i.test(String(e))) throw e;
    }
    prepared.current = true;
  }, [recorder]);
  // The speech queue holds the playback session between its lines: without the hold every clip
  // end re-arms the recorder (RECORD_MODE) and the next clip switches back (PLAYBACK_MODE).
  const playbackHold = useRef(false);
  const audioGen = useRef(0);
  /** The audio mode last set: a tap on an idle, already-armed mic skips the session switch (≈100 ms on iOS). */
  const modeNow = useRef<"record" | "playback" | null>(null);
  const setMode = useCallback(async (m: "record" | "playback") => {
    await setAudioModeAsync(m === "record" ? RECORD_MODE : PLAYBACK_MODE);
    modeNow.current = m;
  }, []);
  /** Live input level 0..1 while recording, for the sheet's meter (a shared value: 20 updates a second must
   *  not re-render the map screen). */
  const level: SharedValue<number> = useSharedValue(0);
  const setLevel = useCallback((v: number) => {
    level.set(v);
  }, [level]);
  const prepareIdle = useCallback(async () => {
    if (Platform.OS === "web" || !prewarm.current || playbackHold.current || prepared.current || recorder.isRecording || player.current) return;
    const gen = audioGen.current;
    const stale = () => gen !== audioGen.current || player.current !== null || recorder.isRecording;
    try {
      const p = await getRecordingPermissionsAsync();
      if (!p.granted || stale()) return;
      await setMode("record");
      if (stale()) return;
      try {
        await ensurePrepared();
      } catch (first) {
        // iOS "Session activation failed" right after a clip: the session needs a moment. One more try.
        await new Promise((r) => setTimeout(r, 300));
        if (stale()) return;
        try {
          await ensurePrepared();
        } catch (e) {
          console.log(`[voice] idle prepare failed: ${String(e)} (first: ${String(first)})`);
        }
      }
    } catch (e) {
      console.log(`[voice] idle prepare failed: ${String(e)}`);
    }
  }, [recorder, ensurePrepared, setMode]);

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
    if (p) releaseAudioSession(); // the radio comes back up, also when the reply was cut short
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
  /** Keep the playback session after the current clip ends (more clips follow); releasing re-arms the mic. */
  const holdPlayback = useCallback(
    (on: boolean) => {
      if (playbackHold.current === on) return;
      playbackHold.current = on;
      if (!on) prepareIdle();
    },
    [prepareIdle],
  );
  /** A clip is on air right now (ref-based, for callbacks; `speaking` is the render-time state). */
  const isSpeaking = useCallback(() => player.current !== null, []);
  /** The mic is open (a clip starting now would flip the session under the recording). */
  const isRecording = useCallback(() => recorder.isRecording, [recorder]);

  /** Starts recording as fast as the platform allows: no session switch when the idle mic is already armed
   *  (prepareIdle), no permission round trip (the screen checks once). Returns ms from the call to record(). */
  const startRecording = useCallback(async () => {
    const t0 = Date.now();
    stopSpeaking();
    audioGen.current++;
    await acquireAudioSession();
    if (modeNow.current !== "record") await setMode("record");
    await ensurePrepared();
    prepared.current = false;
    recorder.record();
    return Date.now() - t0;
  }, [recorder, stopSpeaking, ensurePrepared, setMode]);

  // Returns the local file uri, or null if nothing was recorded.
  const stopRecording = useCallback(async (): Promise<string | null> => {
    if (!recorder.isRecording) return recorder.uri ?? null;
    await recorder.stop();
    setLevel(0);
    await setMode("playback");
    // No re-prepare here: the reply comes next and must play in the playback session.
    // The recorder is prepared again once it finishes (stopSpeaking) or when idle.
    return recorder.uri ?? null;
  }, [recorder, setMode, setLevel]);

  const cancelRecording = useCallback(async () => {
    listenToken.current++;
    if (recorder.isRecording) await recorder.stop();
    setLevel(0);
    await setMode("playback");
    prepareIdle(); // idle again; any reply that starts meanwhile cancels this (audioGen)
  }, [recorder, prepareIdle, setMode, setLevel]);

  /**
   * One turn of the driver: records from the tap until the end of the phrase (src/voice/endpointer.ts: a pause
   * after speech, the 30 s cap) or a second tap (finishListening). The chime and the start haptic come after
   * record(), so words said with the tap are kept. Resolves the file uri; `{uri, maybe}` when there was sound
   * but no clear speech (the server decides); "silent" if nothing at all; or "cancelled".
   */
  const listen = useCallback(
    async ({ maxMs = MAX_MS, silenceMs, startTimeoutMs, chime = true }: ListenOptions = {}): Promise<string | MaybeRecording | "silent" | "cancelled"> => {
      const token = ++listenToken.current;
      finishNow.current = false;
      const armMs = await startRecording();
      if (chime) playCue("start");
      const ep = createEndpointer({ maxMs, endSilenceMs: silenceMs, startTimeoutMs });
      const t0 = Date.now();
      const meter: number[] = []; // dB per poll, for tuning the thresholds on recorded cab profiles (PR 3)
      let hasMeter = false;
      let rearmed = false;
      let ended = "max";
      let maybe = false;
      while (listenToken.current === token) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        if (listenToken.current !== token) break;
        if (finishNow.current) {
          ended = "tap";
          maybe = !ep.stats().spoke; // a tap on a recording with no clear speech: the server decides
          break;
        }
        const status = recorder.getStatus();
        const db = status.metering;
        const t = Date.now() - t0;
        if (!rearmed && t >= 300 && !status.isRecording) {
          // The fast start skipped the session switch on a mic that looked armed; if the recorder did not start,
          // do the full switch once (the old path) instead of recording nothing.
          rearmed = true;
          console.log("[voice] recorder not running after the fast start: re-arming");
          try {
            await setMode("record");
            await ensurePrepared();
            prepared.current = false;
            recorder.record();
          } catch (e) {
            console.log(`[voice] re-arm failed: ${String(e)}`);
          }
          continue;
        }
        if (typeof db === "number" && Number.isFinite(db)) {
          hasMeter = true;
          meter.push(Math.round(db));
          setLevel(Math.max(0, Math.min(1, (db + 60) / 45)));
          const d = ep.push(db, t);
          if (d === "warn") playCue("tick");
          else if (d === "send") {
            ended = ep.stats().reason ?? "silence";
            break;
          } else if (d === "maybe") {
            ended = "maybe";
            maybe = true;
            break;
          } else if (d === "silent") {
            ended = "silent";
            break;
          }
        } else if (!hasMeter && t > NO_METER_MS) {
          ended = "no-meter";
          break;
        } else if (t >= maxMs) {
          ended = "cap";
          break;
        }
      }
      if (listenToken.current !== token) return "cancelled";
      const st = ep.stats();
      console.log(`[voice] turn end=${ended} ms=${Date.now() - t0} arm=${armMs} noise=${st.noiseDb.toFixed(0)} voice=${st.voiceDb === null ? "-" : st.voiceDb.toFixed(0)} peak=${st.peakDb.toFixed(0)}`);
      if (meter.length) console.log(`[voice] meter step=${POLL_MS} ${meter.join(",")}`);
      if (ended === "silent") {
        await cancelRecording();
        return "silent";
      }
      const uri = await stopRecording();
      if (!uri) return "silent";
      playCue("sent");
      return maybe ? { uri, maybe: true } : uri;
    },
    [recorder, startRecording, stopRecording, cancelRecording, setLevel, setMode, ensurePrepared],
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
      acquireAudioSession()
        .then(() => setMode("playback"))
        .catch((e) => console.log(`[voice] playback mode failed: ${String(e)}`))
        .finally(() => {
          if (player.current === p) p.play();
        });
    },
    [stopSpeaking, setMode],
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
    micKnownGranted,
    cue: playCue,
    level,
    requestMic,
    startRecording,
    stopRecording,
    cancelRecording,
    listen,
    setPrewarm,
    holdPlayback,
    isSpeaking,
    isRecording,
    finishListening,
    play,
    preload,
    playAndWait,
    stopSpeaking,
    speaking,
  };
}
