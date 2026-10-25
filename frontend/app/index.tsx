import { useCallback, useEffect, useRef, useState } from "react";
import { StyleSheet, View } from "react-native";
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
import { useVoice, type MicPermission } from "@/src/hooks/useVoice";
import { shadow, spacing, useTheme } from "@/src/theme";
import type { ActiveRoute, AgentResponse, LatLng, ParkingState, Phase, Place } from "@/src/types";
import { openGoogleMaps, openWaze } from "@/src/utils/navLinks";

const LISTEN_WATCHDOG_MS = 30000;
const REQUEST_TIMEOUT_MS = 60000;
const STAGES = ["Searching nearby…", "Checking truck parking from satellite…", "Setting route…"];
const STAGE_MS = 3500;

export default function MapScreen() {
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const loc = useDriverLocation();
  const voice = useVoice();
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
    if (res.audio_url) voice.play(absoluteUrl(res.audio_url));
  };

  const startRoute = (next: ActiveRoute) => {
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

  const submitTurn = async () => {
    clearTimers();
    const myTurn = ++turn.current;
    setPhase("thinking");
    startStages();
    const ctrl = new AbortController();
    inflight.current = ctrl;
    const timer = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
    try {
      const uri = await voice.stopRecording();
      if (!uri) throw new Error("no recording");
      const res = await api.voice(uri, loc.coords, activeRef.current?.place, ctrl.signal);
      if (myTurn !== turn.current) return;
      if (res.status === "route_set" && res.place && res.route) {
        const others = res.options ?? [];
        setOptions(others);
        setParking({});
        setPlaces([res.place, ...others]);
        startRoute({ place: res.place, route: res.route, parking: res.parking });
      } else {
        if (res.status !== "chat") showToast(res.message ?? MESSAGES.notUnderstood);
        backToIdle();
      }
      sayReply(res);
    } catch (e) {
      if (myTurn !== turn.current) return;
      const msg = String(e).includes("502") ? MESSAGES.placesUnavailable : MESSAGES.notUnderstood;
      showToast(msg);
      setCaption({ reply: msg });
      backToIdle();
    } finally {
      clearTimeout(timer);
      if (myTurn === turn.current) clearTimers();
    }
  };

  const beginListening = async () => {
    try {
      await voice.startRecording();
    } catch {
      showToast(MESSAGES.notUnderstood);
      return;
    }
    setCaption(null);
    setPhase("listening");
    clearTimers();
    watchdog.current = setTimeout(submitTurn, LISTEN_WATCHDOG_MS);
  };

  const onAvatarPress = async () => {
    if (phase === "thinking") return;
    if (phase === "listening") {
      submitTurn();
      return;
    }
    voice.stopSpeaking();
    const perm = await voice.micPermission();
    if (perm !== "granted") {
      setMicCard(perm);
      return;
    }
    beginListening();
  };

  const onAllowMic = async () => {
    const perm = await voice.requestMic();
    if (perm === "granted") {
      setMicCard(null);
      beginListening();
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
    voice.stopSpeaking();
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

      <TopBar topInset={insets.top} isDemo={loc.resolved && loc.isDemo} onLayout={setTopH} />

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
        onOpenGoogle={() => active && openGoogleMaps(active.place.lat, active.place.lng)}
        onOpenWaze={() => active && openWaze(active.place.lat, active.place.lng)}
        onLayout={setSheetH}
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
