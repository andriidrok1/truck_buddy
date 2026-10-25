// On-device wake word ("Hey Buddy") via Picovoice Porcupine. Nothing is recorded or sent
// anywhere before the wake word fires. Works only in a dev/production build: in Expo Go,
// without an access key, or on any init error the hook stays inactive (tap still works).
// Web uses useWakeWord.web.ts (no native modules imported there).
import { createAudioPlayer, setAudioModeAsync } from "expo-audio";
import Constants, { ExecutionEnvironment } from "expo-constants";
import { useCallback, useEffect, useRef, useState } from "react";
import { AppState, PermissionsAndroid, Platform } from "react-native";

const ACCESS_KEY = process.env.EXPO_PUBLIC_PICOVOICE_ACCESS_KEY ?? "";
// Copied into the native project by plugins/withWakeWord.js from assets/wakeword/.
const ANDROID_KEYWORD = "hey_buddy_android.ppn"; // relative to android/app/src/main/assets
const IOS_KEYWORD = "hey_buddy_ios.ppn"; // in the main app bundle
const CHIME = require("../../assets/sounds/chime.mp3");
const CHIME_MS = 300;
const FOREGROUND_WAIT_MS = 60000; // wake word heard in background: wait this long for the app to open

export const WAKE_SUPPORTED = !!ACCESS_KEY && Constants.executionEnvironment !== ExecutionEnvironment.StoreClient;

type Manager = { start(): Promise<void>; stop(): Promise<void>; delete(): void };

/** Android: the asset-relative name. iOS: absolute path inside the main bundle. */
function keywordPath() {
  if (Platform.OS === "android") return ANDROID_KEYWORD;
  const { Paths } = require("expo-file-system");
  const dir = decodeURI(String(Paths.bundle.uri).replace(/^file:\/\//, "")).replace(/\/$/, "");
  return `${dir}/${IOS_KEYWORD}`;
}

/** Resolves true once the app is in the foreground, false after `ms`. */
function waitForForeground(ms: number) {
  return new Promise<boolean>((resolve) => {
    const timer = setTimeout(() => {
      sub.remove();
      resolve(false);
    }, ms);
    const sub = AppState.addEventListener("change", (s) => {
      if (s !== "active") return;
      clearTimeout(timer);
      sub.remove();
      resolve(true);
    });
  });
}

/** Android 13+: the foreground service needs a visible notification. */
async function notificationsAllowed() {
  if (Platform.OS !== "android" || Number(Platform.Version) < 33) return true;
  const perm = PermissionsAndroid.PERMISSIONS.POST_NOTIFICATIONS;
  if (await PermissionsAndroid.check(perm)) return true;
  return (await PermissionsAndroid.request(perm)) === PermissionsAndroid.RESULTS.GRANTED;
}

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

  const fail = (e: unknown) => {
    broken.current = true;
    running.current = false;
    setActive(false);
    setError(String((e as { message?: string })?.message ?? e));
  };

  // Start/stop calls are serialized so the engine never gets two at once.
  const run = useCallback((fn: () => Promise<void>) => {
    queue.current = queue.current.then(fn).catch(fail);
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
      if (!(await notificationsAllowed())) {
        console.warn("[wakeword] Notifications denied: wake word works only while the app is on screen.");
        return;
      }
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
        // iOS: Porcupine needs a playAndRecord session. expo-audio sets it with .defaultToSpeaker
        // (shouldRouteThroughEarpiece: false), so TTS and the chime stay on the loud speaker.
        if (Platform.OS === "ios") {
          await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true, shouldRouteThroughEarpiece: false });
        }
        await manager.current.start();
        running.current = true;
        setActive(true);
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [run, phrase],
  );

  const onDetected = () => {
    run(async () => {
      await stopEngine(); // free the mic for the recorder
      const chime = createAudioPlayer(CHIME);
      chime.play();
      await new Promise((r) => setTimeout(r, CHIME_MS));
      chime.remove();
    }).then(async () => {
      if (broken.current) return;
      // Android 14 forbids starting a recording from the background: wait for the driver to open the app.
      if (AppState.currentState !== "active" && !(await waitForForeground(FOREGROUND_WAIT_MS))) {
        start();
        return;
      }
      onWakeRef.current();
    });
  };

  // Engine error (e.g. iOS audio session changed under it): stop, and fall back to "Tap to talk".
  const onEngineError = (e: { message?: string }) =>
    run(async () => {
      const m = manager.current;
      running.current = false;
      if (m) await m.stop().catch(() => {});
      fail(e?.message ?? "wake word error");
    });

  const create = async (): Promise<{ m: Manager; label: string }> => {
    const { PorcupineManager, BuiltInKeywords } = require("@picovoice/porcupine-react-native");
    try {
      const m = await PorcupineManager.fromKeywordPaths(ACCESS_KEY, [keywordPath()], onDetected, onEngineError);
      return { m, label: "Hey Buddy" };
    } catch (e) {
      // Fallback so hands-free still works without the custom keyword files.
      console.warn(`[wakeword] "Hey Buddy" keyword not loaded (${String((e as Error)?.message ?? e)}); using built-in "Jarvis".`);
      const m = await PorcupineManager.fromBuiltInKeywords(ACCESS_KEY, [BuiltInKeywords.JARVIS], onDetected, onEngineError);
      return { m, label: "Jarvis" };
    }
  };

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
