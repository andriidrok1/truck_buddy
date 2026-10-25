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

// Microphone recording + reply playback (expo-audio).
export function useVoice() {
  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const player = useRef<AudioPlayer | null>(null);
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
    if (recorder.isRecording) await recorder.stop();
    await setAudioModeAsync({ allowsRecording: false, playsInSilentMode: true });
  }, [recorder]);

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

  useEffect(() => () => player.current?.remove(), []);

  return { micPermission, requestMic, startRecording, stopRecording, cancelRecording, play, stopSpeaking, speaking };
}
