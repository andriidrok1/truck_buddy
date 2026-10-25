import { useFocusEffect, useRouter } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import { AppState, StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { absoluteUrl, api } from "@/src/api";
import { CopilotSheet } from "@/src/components/CopilotSheet";
import { Icon } from "@/src/components/Icon";
import MapLayer from "@/src/components/MapLayer";
import type { MapLayerHandle } from "@/src/components/mapTypes";
import { MicPermissionCard } from "@/src/components/MicPermissionCard";
import { PressableScale } from "@/src/components/PressableScale";
import { CaptionBubble, Toast, type ToastData } from "@/src/components/Toast";
import { TopBar } from "@/src/components/TopBar";
import { MESSAGES } from "@/src/constants";
import { useDriverLocation } from "@/src/hooks/useDriverLocation";
import { isOnboarded, truckLabel, useHandsFree, useTruckProfile } from "@/src/hooks/useTruckProfile";
import { useVoice, type MicPermission } from "@/src/hooks/useVoice";
import { useWakeWord } from "@/src/hooks/useWakeWord";
import { shadow, spacing, useTheme } from "@/src/theme";
import type { ActiveRoute, AgentResponse, LatLng, ParkingState, Phase, Place } from "@/src/types";

const REQUEST_TIMEOUT_MS = 60000;
const STAGES = ["Searching nearby…", "Setting route…"];
const STAGE_MS = 3500;
const WAKE_SLEEP_MS = 10 * 60 * 1000; // background without a route: turn the wake word off
const FILLERS = [
  "Alright, let me look.",
  "Okay, one sec.",
  "Got it, checking.",
  "Hang on.",
  "Let me see what's around.",
  "On it.",
  "Sure, one moment.",
  "Checking.",
];

export default function MapScreen() {
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const loc = useDriverLocation();
  const voice = useVoice();
  const truck = useTruckProfile();
  const router = useRouter();
  const mapRef = useRef<MapLayerHandle>(null);
  const center = useRef<LatLng>(loc.coords);

  const [topH, setTopH] = useState(140);
  const [sheetH, setSheetH] = useState(260);
  const [phase, setPhase] = useState<Phase>("ready");
  const [active, setActive] = useState<ActiveRoute | null>(null);
  const [places, setPlaces] = useState<Place[]>([]);
  const [toast, setToast] = useState<ToastData | null>(null);
  const [caption, setCaption] = useState<{ reply: string; heard?: string } | null>(null);
  const [micCard, setMicCard] = useState<Exclude<MicPermission, "granted"> | null>(null);
  const [fit, setFit] = useState<{ key: string; points: LatLng[] }>({ key: "init", points: [] });
  const [thinkingText, setThinkingText] = useState<string | undefined>(undefined);
  const [options, setOptions] = useState<Place[]>([]);
  const [parking, setParking] = useState<Record<string, ParkingState>>({});

  const turn = useRef(0);
  const watchdog = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stageTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const inflight = useRef<AbortController | null>(null);
  const activeRef = useRef<ActiveRoute | null>(null);
  activeRef.current = active;
  const optionsRef = useRef<Place[]>([]);
  optionsRef.current = options;

  const showToast = useCallback((text: string, kind: ToastData["kind"] = "error") => {
    setToast({ id: Date.now(), text, kind });
  }, []);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 3200);
    return () => clearTimeout(t);
  }, [toast]);

  useEffect(() => {
    if (!caption || voice.speaking) return;
    const t = setTimeout(() => setCaption(null), 8000);
    return () => clearTimeout(t);
  }, [caption, voice.speaking]);

  // First launch: meet the copilot.
  useEffect(() => {
    isOnboarded().then((ok) => {
      if (!ok) router.replace("/onboarding");
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Break reminder while a route is active.
  const routeStart = useRef<number | null>(null);
  useEffect(() => {
    if (!active || !truck.break_hours) return;
    const limitMs = truck.break_hours * 3600 * 1000;
    const t = setInterval(() => {
      if (!routeStart.current || Date.now() - routeStart.current < limitMs) return;
      routeStart.current = Date.now(); // remind again after the next interval
      const who = truck.driver_name ? `Hey ${truck.driver_name}, ` : "";
      const hrs = truck.break_hours === 1 ? "an hour" : `${truck.break_hours} hours`;
      const text = `${who}you've been driving for ${hrs}. Want me to find a rest area?`;
      setCaption({ reply: text });
      api
        .speak(text)
        .then((r) => voice.play(absoluteUrl(r.audio_url)))
        .catch(() => {});
    }, 60000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, truck.break_hours, truck.driver_name]);

  // Re-center once when the real GPS fix arrives.
  useEffect(() => {
    if (!loc.isDemo) {
      center.current = loc.coords;
      setFit({ key: "gps", points: [loc.coords] });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loc.isDemo]);

  // Fit all option pins once the (taller) options sheet has been laid out.
  useEffect(() => {
    if (phase === "options" && options.length) {
      setFit({ key: `options-${sheetH}-${options.length}`, points: [loc.coords, ...options] });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, sheetH, options]);

  const sayReply = (res: AgentResponse) => {
    setCaption({ reply: res.reply, heard: res.transcript || undefined });
    if (!res.audio_url) return Promise.resolve();
    const [first, ...restParts] = res.reply.split(/(?<=[.!?])\s+/);
    const rest = restParts.join(" ");
    const done = voice.playAndWait(absoluteUrl(res.audio_url), Math.min(20000, 1500 + first.length * 65));
    if (!res.audio_url_2) return done;
    const myTurn = turn.current;
    return done.then(() =>
      myTurn === turn.current
        ? voice.playAndWait(absoluteUrl(res.audio_url_2!), Math.min(20000, 1500 + (rest || res.reply).length * 65))
        : undefined,
    );
  };

  // Instant acknowledgement while the real answer is on its way (cached TTS, rotated).
  const fillers = useRef<string[]>([]);
  const lastFiller = useRef(-1);
  useEffect(() => {
    Promise.allSettled(FILLERS.map((t) => api.speak(t).then((r) => absoluteUrl(r.audio_url)))).then((all) => {
      fillers.current = all.flatMap((r) => (r.status === "fulfilled" ? [r.value] : []));
    });
  }, []);
  /** Random acknowledgement, never the same twice in a row; skipped one time in four. */
  const playFiller = () => {
    const n = fillers.current.length;
    if (!n || Math.random() < 0.25) return;
    let i = Math.floor(Math.random() * n);
    if (n > 1 && i === lastFiller.current) i = (i + 1 + Math.floor(Math.random() * (n - 1))) % n;
    lastFiller.current = i;
    voice.play(fillers.current[i]);
  };

  // Warm Places + parking caches around the driver: app on screen, no active route,
  // real GPS fix (or demo after location resolved), then again after moving ~5 km.
  const warmedAt = useRef<LatLng | null>(null);
  const [appActive, setAppActive] = useState(AppState.currentState === "active");
  useEffect(() => {
    const sub = AppState.addEventListener("change", (s) => setAppActive(s === "active"));
    return () => sub.remove();
  }, []);
  useEffect(() => {
    if (!appActive || active || (loc.isDemo && !loc.resolved)) return;
    const w = warmedAt.current;
    if (w && Math.hypot(w.lat - loc.coords.lat, (w.lng - loc.coords.lng) * 0.8) < 0.045) return;
    warmedAt.current = loc.coords;
    api.warm(loc.coords).catch(() => {});
  }, [loc.coords, loc.isDemo, loc.resolved, appActive, active]);

  /** Satellite parking check runs after the route is set; its directions are spoken when the reply ends. */
  const followParking = async (place: Place, replyDone: Promise<void>, myTurn: number) => {
    try {
      const pk = await api.parking(place);
      // The card always takes the result if it's still the same destination.
      setActive((a) => (a && a.place.id === place.id ? { ...a, parking: pk } : a));
      if (myTurn !== turn.current || activeRef.current?.place.id !== place.id) return;
      const tips = [pk.entry_tip, pk.park_tip].filter(Boolean).join(". ");
      const text =
        pk.verdict === "unlikely"
          ? "Heads up: from the satellite, parking there looks tight for a rig."
          : tips
            ? `${tips}.`
            : "";
      if (!text) return;
      const audio = api.speak(text); // generate while the first answer is still playing
      await replyDone;
      if (myTurn !== turn.current || activeRef.current?.place.id !== place.id) return;
      setCaption({ reply: text });
      const r = await audio;
      if (myTurn === turn.current && activeRef.current?.place.id === place.id) {
        await voice.playAndWait(absoluteUrl(r.audio_url), Math.min(15000, 1500 + text.length * 65));
      }
    } catch {
      // parking stays "unclear"; the route still works
    }
  };

  // ---- Live dialog: one tap starts it, the copilot keeps listening after each answer. ----
  const dialog = useRef<string | null>(null);
  const misses = useRef(0);
  const endDialog = () => {
    dialog.current = null;
    misses.current = 0;
  };
  const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

  /** After an answer: keep talking unless the conversation is over. */
  const continueDialog = async (myTurn: number, res: AgentResponse | null, spoken: Promise<void>) => {
    if (!dialog.current) return;
    misses.current = res && res.status !== "not_understood" ? 0 : misses.current + 1;
    if (res?.end_conversation || misses.current >= 2) {
      endDialog();
      return;
    }
    await spoken;
    if (myTurn !== turn.current || !dialog.current) return;
    listenNow();
  };

  const startRoute = (next: ActiveRoute) => {
    if (!activeRef.current) routeStart.current = Date.now();
    setActive(next);
    setPhase("navigating");
    setFit({ key: `route-${next.place.id}-${Date.now()}`, points: [loc.coords, next.place] });
  };

  const clearTimers = () => {
    if (watchdog.current) clearTimeout(watchdog.current);
    if (stageTimer.current) clearInterval(stageTimer.current);
    watchdog.current = null;
    stageTimer.current = null;
  };

  const startStages = () => {
    let i = 0;
    setThinkingText(STAGES[0]);
    stageTimer.current = setInterval(() => {
      i = Math.min(i + 1, STAGES.length - 1);
      setThinkingText(STAGES[i]);
    }, STAGE_MS);
  };

  const backToIdle = () =>
    setPhase(activeRef.current ? "navigating" : optionsRef.current.length ? "options" : "ready");

  const submitTurn = async (recorded?: string) => {
    clearTimers();
    const myTurn = ++turn.current;
    setPhase("thinking");
    startStages();
    const ctrl = new AbortController();
    inflight.current = ctrl;
    const timer = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
    try {
      const uri = recorded ?? (await voice.stopRecording());
      if (!uri) throw new Error("no recording");
      const { transcript } = await api.transcribe(uri, ctrl.signal);
      if (myTurn !== turn.current) return;
      if (transcript) {
        playFiller(); // only when the driver actually said something
        setCaption({ reply: "…", heard: transcript });
      }
      const res = await api.agentSpeak(transcript, loc.coords, activeRef.current, ctrl.signal, dialog.current ?? undefined);
      if (myTurn !== turn.current) return;
      if (res.status === "route_set" && res.place && res.route) {
        const others = res.options ?? [];
        setOptions(others);
        setParking({});
        setPlaces([res.place, ...others]);
        startRoute({ place: res.place, route: res.route, parking: res.parking });
        const replyDone = sayReply(res);
        if (res.place.category !== "destination") followParking(res.place, replyDone, myTurn);
        endDialog(); // route is set: stop listening until the driver taps the mic again
      } else {
        if (res.status !== "chat" && res.status !== "not_understood") showToast(res.message ?? MESSAGES.notUnderstood);
        backToIdle();
        continueDialog(myTurn, res, sayReply(res));
      }
    } catch (e) {
      if (myTurn !== turn.current) return;
      const err = String(e);
      const msg = err.includes("422")
        ? "Check your truck profile and try again."
        : err.includes("502")
          ? MESSAGES.placesUnavailable
          : MESSAGES.notUnderstood;
      setCaption({ reply: msg });
      backToIdle();
      continueDialog(myTurn, null, wait(1200));
    } finally {
      clearTimeout(timer);
      if (myTurn === turn.current) clearTimers();
    }
  };

  // Hands-free: listening stops by itself when the driver pauses (tap to stop early).
  const beginListening = async () => {
    if (!dialog.current) dialog.current = `dlg-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    setPhase("listening");
    clearTimers();
    const myTurn = ++turn.current;
    let res: string;
    try {
      res = await voice.listen({ silenceMs: 700 });
    } catch {
      endDialog();
      showToast(MESSAGES.notUnderstood);
      backToIdle();
      return;
    }
    if (res === "cancelled" || myTurn !== turn.current) return;
    if (res === "silent") {
      // Silence ends the conversation; on the very first turn tell the driver we heard nothing.
      if (misses.current === 0 && !caption) setCaption({ reply: MESSAGES.notUnderstood });
      endDialog();
      backToIdle();
      return;
    }
    submitTurn(res);
  };

  // ---- Hands-free wake word: same beginListening() as a tap, no separate dialog logic. ----
  const handsFree = useHandsFree();
  const [micOk, setMicOk] = useState(false);
  const [focused, setFocused] = useState(true);
  const [asleep, setAsleep] = useState(false);
  const wake = useWakeWord(() => {
    // Barge-in: the wake word interrupts the copilot's speech and starts listening.
    voice.stopSpeaking();
    if (!dialog.current) setCaption(null);
    beginListening();
  });
  /** The recorder and the wake word can't share the mic (Android): pause it first. */
  const listenNow = async () => {
    await wake.stop();
    beginListening();
  };

  const { micPermission } = voice;
  const checkMic = useCallback(() => {
    micPermission().then((p) => setMicOk(p === "granted")).catch(() => setMicOk(false));
  }, [micPermission]);

  // Modal screens (onboarding, truck profile) pause the wake word.
  useFocusEffect(
    useCallback(() => {
      setFocused(true);
      checkMic();
      return () => setFocused(false);
    }, [checkMic]),
  );

  // Battery: in the background with no route for 10 min, turn it off; back in the app, on again.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const sub = AppState.addEventListener("change", (s) => {
      if (timer) clearTimeout(timer);
      timer = null;
      if (s === "active") {
        setAsleep(false);
        checkMic();
      } else if (s === "background") {
        timer = setTimeout(() => {
          if (!activeRef.current) setAsleep(true);
        }, WAKE_SLEEP_MS);
      }
    });
    return () => {
      sub.remove();
      if (timer) clearTimeout(timer);
    };
  }, [checkMic]);

  const idle = phase === "ready" || phase === "navigating" || phase === "options";
  const wakeArmed = wake.supported && handsFree && micOk && !asleep;
  useEffect(() => {
    if (!wakeArmed) wake.shutdown();
    else if (focused && idle) wake.start();
    else wake.stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wakeArmed, focused, idle]);

  const onAvatarPress = async () => {
    if (phase === "thinking") return;
    if (phase === "listening") {
      voice.finishListening();
      return;
    }
    voice.stopSpeaking();
    const perm = await voice.micPermission();
    if (perm !== "granted") {
      setMicCard(perm);
      return;
    }
    if (!dialog.current) setCaption(null);
    listenNow();
  };

  const onAllowMic = async () => {
    const perm = await voice.requestMic();
    if (perm === "granted") {
      setMicCard(null);
      setMicOk(true);
      listenNow();
    } else {
      // Asked once with context; after that only Settings can fix it.
      setMicCard("blocked");
    }
  };

  const onOptionParking = async (p: Place) => {
    setParking((s) => ({ ...s, [p.id]: { loading: true } }));
    try {
      const result = await api.parking(p);
      setParking((s) => ({ ...s, [p.id]: { loading: false, result } }));
    } catch {
      setParking((s) => ({ ...s, [p.id]: { loading: false } }));
      showToast("Couldn't check parking. Try again.");
    }
  };

  const onClose = () => {
    endDialog();
    voice.stopSpeaking();
    clearTimers();
    turn.current++; // discard any in-flight turn
    inflight.current?.abort();
    voice.cancelRecording().catch(() => {});
    if (phase === "options") {
      // Back to the route card if one is active, otherwise clear the list.
      if (activeRef.current) {
        setPhase("navigating");
        return;
      }
      setOptions([]);
      setParking({});
      setPlaces([]);
      setCaption(null);
      setPhase("ready");
      return;
    }
    backToIdle();
  };

  const onEndRoute = () => {
    endDialog();
    turn.current++;
    voice.cancelRecording().catch(() => {});
    voice.stopSpeaking();
    routeStart.current = null;
    setActive(null);
    setCaption(null);
    setOptions([]);
    setParking({});
    setPlaces([]);
    setPhase("ready");
    setFit({ key: `end-${Date.now()}`, points: [loc.coords] });
  };

  const onPlacePress = async (p: Place) => {
    if (phase === "thinking") return;
    const prev = activeRef.current;
    const myTurn = ++turn.current;
    voice.stopSpeaking();
    setCaption(null);
    setPhase("thinking");
    setThinkingText(STAGES[1]);
    try {
      const res = await api.route(loc.coords, p);
      if (myTurn !== turn.current) return;
      // Keep the previous destination among the alternatives.
      setOptions((list) => [...(prev ? [prev.place] : []), ...list].filter((o) => o.id !== p.id));
      startRoute(res);
    } catch {
      if (myTurn !== turn.current) return;
      backToIdle();
      showToast("Couldn't build a route. Try again.");
    }
  };

  useEffect(() => () => clearTimers(), []);

  return (
    <View style={[styles.root, { backgroundColor: colors.mapPlaceholder }]} testID="map-screen">
      <MapLayer
        ref={mapRef}
        driver={loc.coords}
        heading={loc.heading}
        places={places}
        active={active}
        topInset={topH}
        bottomInset={sheetH}
        fitKey={fit.key}
        fitPoints={fit.points}
        onCenterChange={(c) => (center.current = c)}
        onPlacePress={onPlacePress}
      />

      <TopBar
        topInset={insets.top}
        truckLabel={truckLabel(truck)}
        hazmat={truck.hazmat}
        onTruckPress={() => router.push("/truck-profile")}
        onMenuPress={() => router.push("/onboarding")}
        isDemo={loc.resolved && loc.isDemo}
        onLayout={setTopH}
      />

      <PressableScale
        testID="recenter-button"
        accessibilityLabel="Recenter"
        onPress={() => mapRef.current?.recenter()}
        style={[styles.recenter, shadow(colors), { backgroundColor: colors.surface, bottom: sheetH + spacing.lg }]}
      >
        <Icon name="crosshairs-gps" size={24} color={colors.info} />
      </PressableScale>

      <CaptionBubble text={caption?.reply ?? null} heard={caption?.heard} bottom={sheetH + spacing.lg} />

      <CopilotSheet
        phase={phase}
        speaking={voice.speaking}
        onShowOptions={() => setPhase("options")}
        thinkingText={thinkingText}
        options={options}
        parking={parking}
        onOptionParking={onOptionParking}
        onOptionRoute={onPlacePress}
        active={active}
        bottomInset={insets.bottom}
        onAvatarPress={onAvatarPress}
        onClose={onClose}
        onEndRoute={onEndRoute}
        onLayout={setSheetH}
        wakePhrase={wake.active ? wake.phrase : null}
      />

      {micCard && (
        <MicPermissionCard
          state={micCard}
          bottom={sheetH + spacing.lg}
          onAllow={onAllowMic}
          onDismiss={() => setMicCard(null)}
        />
      )}

      <Toast toast={toast} top={topH + spacing.sm} />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  recenter: {
    position: "absolute",
    right: spacing.lg,
    width: 56,
    height: 56,
    borderRadius: 28,
    alignItems: "center",
    justifyContent: "center",
  },
});
