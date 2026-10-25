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
import type { ActiveRoute, AgentResponse, Category, LatLng, Phase, Place } from "@/src/types";
import { openGoogleMaps, openWaze } from "@/src/utils/navLinks";

const LISTEN_WATCHDOG_MS = 30000;
const REQUEST_TIMEOUT_MS = 45000;

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
  const [category, setCategory] = useState<Category | null>(null);
  const [loadingCat, setLoadingCat] = useState<Category | null>(null);
  const [places, setPlaces] = useState<Place[]>([]);
  const [toast, setToast] = useState<ToastData | null>(null);
  const [caption, setCaption] = useState<{ reply: string; heard?: string } | null>(null);
  const [micCard, setMicCard] = useState<Exclude<MicPermission, "granted"> | null>(null);
  const [fit, setFit] = useState<{ key: string; points: LatLng[] }>({ key: "init", points: [] });

  const turn = useRef(0);
  const watchdog = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inflight = useRef<AbortController | null>(null);
  const activeRef = useRef<ActiveRoute | null>(null);
  activeRef.current = active;

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

  const sayReply = (res: AgentResponse) => {
    setCaption({ reply: res.reply, heard: res.transcript || undefined });
    if (res.audio_url) voice.play(absoluteUrl(res.audio_url));
  };

  const startRoute = (next: ActiveRoute) => {
    setActive(next);
    setPhase("navigating");
    setFit({ key: `route-${next.place.id}-${Date.now()}`, points: [loc.coords, next.place] });
  };

  const onSelectCategory = async (c: Category) => {
    if (category === c) {
      setCategory(null);
      setPlaces([]);
      return;
    }
    setCategory(c);
    setLoadingCat(c);
    try {
      const res = await api.places(c, center.current);
      setPlaces(res.places);
      if (res.places.length === 0) showToast(MESSAGES.noPlaces);
      else if (!active) setFit({ key: `cat-${c}-${Date.now()}`, points: [loc.coords, ...res.places.slice(0, 6)] });
    } catch {
      setPlaces([]);
      showToast(MESSAGES.noPlaces);
    } finally {
      setLoadingCat(null);
    }
  };

  const clearWatchdog = () => {
    if (watchdog.current) clearTimeout(watchdog.current);
    watchdog.current = null;
  };

  const backToIdle = () => setPhase(activeRef.current ? "navigating" : "ready");

  const failTurn = () => {
    showToast(MESSAGES.notUnderstood);
    setCaption({ reply: MESSAGES.notUnderstood });
    backToIdle();
  };

  const submitTurn = async () => {
    clearWatchdog();
    const myTurn = ++turn.current;
    setPhase("thinking");
    const ctrl = new AbortController();
    inflight.current = ctrl;
    const timer = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
    try {
      const uri = await voice.stopRecording();
      if (!uri) throw new Error("no recording");
      const res = await api.voice(uri, loc.coords, activeRef.current?.place.id, ctrl.signal);
      if (myTurn !== turn.current) return;
      if (res.status === "route_set" && res.place && res.route) {
        startRoute({ place: res.place, route: res.route });
      } else {
        if (res.status !== "chat") showToast(res.message ?? MESSAGES.notUnderstood);
        backToIdle();
      }
      sayReply(res);
    } catch {
      if (myTurn !== turn.current) return;
      failTurn();
    } finally {
      clearTimeout(timer);
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
    clearWatchdog();
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

  const onClose = () => {
    clearWatchdog();
    turn.current++; // discard any in-flight turn
    inflight.current?.abort();
    voice.cancelRecording().catch(() => {});
    backToIdle();
  };

  const onEndRoute = () => {
    voice.stopSpeaking();
    setActive(null);
    setCaption(null);
    setPhase("ready");
    setFit({ key: `end-${Date.now()}`, points: [loc.coords] });
  };

  const onPlacePress = async (p: Place) => {
    try {
      const res = await api.route(loc.coords, p.id);
      startRoute(res);
    } catch {
      showToast("Couldn't build a route. Try again.");
    }
  };

  useEffect(
    () => () => {
      clearWatchdog();
    },
    [],
  );

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
        selected={category}
        loading={loadingCat}
        isDemo={loc.resolved && loc.isDemo}
        onSelect={onSelectCategory}
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
