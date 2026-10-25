// On-device wake word ("Hey Buddy") via Picovoice Porcupine. Nothing is recorded or sent
// anywhere before the wake word fires. Works only in a dev/production build: in Expo Go,
// without an access key, or on any init error the hook stays inactive (tap still works).
// Web uses useWakeWord.web.ts (no native modules imported there).
import { createAudioPlayer, setAudioModeAsync } from "expo-audio";
import Constants, { ExecutionEnvironment } from "expo-constants";
import { useCallback, useEffect, useRef, useState } from "react";
import { Platform } from "react-native";

const ACCESS_KEY = process.env.EXPO_PUBLIC_PICOVOICE_ACCESS_KEY ?? "";
// Copied into the native project by plugins/withWakeWord.js from assets/wakeword/.
const CUSTOM_KEYWORD = Platform.OS === "ios" ? "hey_buddy_ios.ppn" : "hey_buddy_android.ppn";
const CHIME = require("../../assets/sounds/chime.mp3");
const CHIME_MS = 300;

export const WAKE_SUPPORTED = !!ACCESS_KEY && Constants.executionEnvironment !== ExecutionEnvironment.StoreClient;

type Manager = { start(): Promise<void>; stop(): Promise<void>; delete(): void };

export function useWakeWord(onWake: () => void) {
  const [active, setActive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [phrase, setPhrase] = useState("Hey Buddy");
  const manager = useRef<Manager | null>(null);
  const running = useRef(false);
  const broken = useRef(!WAKE_SUPPORTED);
  const queue = useRef<Promise<void>>(Promise.resolve());
  const releaseService = useRef<(() => void) | null>(null);
  const onWakeRef = useRef(onWake);
  onWakeRef.current = onWake;

  // Start/stop calls are serialized so the engine never gets two at once.
  const run = useCallback((fn: () => Promise<void>) => {
    queue.current = queue.current.then(fn).catch((e) => {
      broken.current = true;
      running.current = false;
      setActive(false);
      setError(String(e?.message ?? e));
    });
    return queue.current;
  }, []);

  const stopEngine = async () => {
    if (!running.current || !manager.current) return;
    running.current = false;
    setActive(false);
    await manager.current.stop();
  };

  // Android: keep the mic alive with the screen off (foreground service + notification).
  const service = async (on: boolean, label: string) => {
    if (Platform.OS !== "android" || !WAKE_SUPPORTED) return;
    const BackgroundService = require("react-native-background-actions").default;
    if (on && !BackgroundService.isRunning()) {
      await BackgroundService.start(
        () => new Promise<void>((resolve) => (releaseService.current = resolve)),
        {
          taskName: "TruckBuddyWakeWord",
          taskTitle: "TruckBuddy is listening",
          taskDesc: `Say "${label}" to talk to your copilot`,
          taskIcon: { name: "ic_launcher", type: "mipmap" },
          foregroundServiceType: ["microphone"],
        },
      );
    } else if (!on && BackgroundService.isRunning()) {
      releaseService.current?.();
      releaseService.current = null;
      await BackgroundService.stop();
    }
  };

  const onDetected = () =>
    run(async () => {
      await stopEngine(); // free the mic for the recorder
      const chime = createAudioPlayer(CHIME);
      chime.play();
      await new Promise((r) => setTimeout(r, CHIME_MS));
      chime.remove();
      onWakeRef.current();
    });

  const create = async (): Promise<{ m: Manager; label: string }> => {
    const { PorcupineManager, BuiltInKeywords } = require("@picovoice/porcupine-react-native");
    const onError = (e: { message?: string }) => setError(e?.message ?? "wake word error");
    try {
      const m = await PorcupineManager.fromKeywordPaths(ACCESS_KEY, [CUSTOM_KEYWORD], onDetected, onError);
      return { m, label: "Hey Buddy" };
    } catch {
      // TODO(wakeword): remove this fallback once hey_buddy_android.ppn / hey_buddy_ios.ppn
      // are trained in Picovoice Console and placed in assets/wakeword/.
      const m = await PorcupineManager.fromBuiltInKeywords(ACCESS_KEY, [BuiltInKeywords.JARVIS], onDetected, onError);
      return { m, label: "Jarvis" };
    }
  };

  /** Start listening for the wake word (no-op when unsupported or already running). */
  const start = useCallback(
    () =>
      run(async () => {
        if (broken.current || running.current) return;
        let label = phrase;
        if (!manager.current) {
          const made = await create();
          manager.current = made.m;
          label = made.label;
          setPhrase(label);
        }
        await service(true, label);
        // iOS: keep the shared audio session record-capable so barge-in works while TTS plays.
        if (Platform.OS === "ios") await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
        await manager.current.start();
        running.current = true;
        setActive(true);
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [run, phrase],
  );

  /** Pause (e.g. while the copilot records the driver). */
  const stop = useCallback(() => run(stopEngine), [run]);

  /** Pause and drop the Android foreground service (hands-free off / battery sleep). */
  const shutdown = useCallback(
    () =>
      run(async () => {
        await stopEngine();
        await service(false, "");
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [run],
  );

  useEffect(
    () => () => {
      run(async () => {
        await stopEngine();
        await service(false, "");
        manager.current?.delete();
        manager.current = null;
      });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  return { active, error, phrase, start, stop, shutdown, supported: WAKE_SUPPORTED };
}
