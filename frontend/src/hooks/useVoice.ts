import {
  createAudioPlayer,
  getRecordingPermissionsAsync,
  RecordingPresets,
  requestRecordingPermissionsAsync,
  setAudioModeAsync,
  useAudioRecorder,
  type AudioPlayer,
} from "expo-audio";
import { useCallback, useEffect, useRef, useState } from "react";

export type MicPermission = "granted" | "ask" | "blocked";

const SPEECH_DB = -38; // louder than this = the driver is talking
const SILENCE_DB = -45; // quieter than this = pause
const POLL_MS = 150;

type ListenOptions = { maxMs?: number; silenceMs?: number; startTimeoutMs?: number };

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

  const stopSpeaking = useCallback(() => {
    player.current?.remove();
    player.current = null;
    setSpeaking(false);
    playDone.current?.();
    playDone.current = null;
  }, []);

  const startRecording = useCallback(async () => {
    stopSpeaking();
    await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
    await recorder.prepareToRecordAsync();
    recorder.record();
  }, [recorder, stopSpeaking]);

  // Returns the local file uri, or null if nothing was recorded.
  const stopRecording = useCallback(async (): Promise<string | null> => {
    if (!recorder.isRecording) return recorder.uri ?? null;
    await recorder.stop();
    await setAudioModeAsync({ allowsRecording: false, playsInSilentMode: true });
    return recorder.uri ?? null;
  }, [recorder]);

  const cancelRecording = useCallback(async () => {
    listenToken.current++;
    if (recorder.isRecording) await recorder.stop();
    await setAudioModeAsync({ allowsRecording: false, playsInSilentMode: true });
  }, [recorder]);

  /**
   * Hands-free answer: records until the driver stops talking.
   * Resolves the file uri, "silent" if nobody spoke, or "cancelled".
   */
  const listen = useCallback(
    async ({ maxMs = 15000, silenceMs = 1100, startTimeoutMs = 7000 }: ListenOptions = {}) => {
      const token = ++listenToken.current;
      finishNow.current = false;
      await startRecording();
      const t0 = Date.now();
      let spoke = false;
      let quietSince = 0;
      let hasMeter = false;
      while (listenToken.current === token) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        if (listenToken.current !== token) break;
        if (finishNow.current) {
          spoke = true;
          break;
        }
        const db = recorder.getStatus().metering;
        const now = Date.now();
        if (typeof db === "number" && Number.isFinite(db)) {
          hasMeter = true;
          if (db > SPEECH_DB) {
            spoke = true;
            quietSince = 0;
          } else if (db < SILENCE_DB && spoke) {
            quietSince = quietSince || now;
            if (now - quietSince >= silenceMs) break;
          }
        }
        if (!spoke && hasMeter && now - t0 > startTimeoutMs) {
          await cancelRecording();
          return "silent" as const;
        }
        // No level meter on this device: fixed answer window.
        if (!hasMeter && now - t0 > 7000) {
          spoke = true;
          break;
        }
        if (now - t0 > maxMs) break;
      }
      if (listenToken.current !== token) return "cancelled" as const;
      return (await stopRecording()) ?? ("silent" as const);
    },
    [recorder, startRecording, stopRecording, cancelRecording],
  );

  const play = useCallback(
    (uri: string) => {
      stopSpeaking();
      const p = createAudioPlayer({ uri });
      player.current = p;
      setSpeaking(true);
      p.addListener("playbackStatusUpdate", (s) => {
        if (s.didJustFinish && player.current === p) stopSpeaking();
      });
      p.play();
    },
    [stopSpeaking],
  );

  /**
   * Plays and resolves when finished. `maxMs` only guards playback that never starts
   * (e.g. autoplay blocked); once audio plays, it runs to the end (real duration + margin).
   */
  const playAndWait = useCallback(
    (uri: string, maxMs: number) =>
      new Promise<void>((resolve) => {
        play(uri);
        const p = player.current;
        let timer = setTimeout(() => stopSpeaking(), Math.max(maxMs, 6000));
        let started = false;
        p?.addListener("playbackStatusUpdate", (s) => {
          if (started || player.current !== p || !s.playing) return;
          started = true;
          clearTimeout(timer);
          const left = s.duration > 0 ? (s.duration - (s.currentTime || 0)) * 1000 : 60000;
          timer = setTimeout(() => player.current === p && stopSpeaking(), left + 2500);
        });
        playDone.current = () => {
          clearTimeout(timer);
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
    finishListening,
    play,
    playAndWait,
    stopSpeaking,
    speaking,
  };
}
