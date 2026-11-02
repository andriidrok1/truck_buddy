import { useMemo, useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";
import { Redirect, useRouter } from "expo-router";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { isTester } from "@/src/auth";
import { Icon } from "@/src/components/Icon";
import { PressableScale } from "@/src/components/PressableScale";
import { clearAnnouncements, useAnnouncements, type AnnouncementKind } from "@/src/dev/announcements";
import { cumulativeMeters } from "@/src/geo/guidance";
import { syntheticTrace, type TracePoint } from "@/src/geo/trace";
import { useOnline } from "@/src/hooks/useOnline";
import { recorder, useRecorder } from "@/src/geo/driveRecorder";
import { requestReplay } from "@/src/geo/replayRequest";
import { listTraces, readTrace, shareTrace } from "@/src/geo/traceFiles";
import { tripLog, useTripLog } from "@/src/net/tripEvents";
import { isTracePlaying, playTrace, SCENARIOS, setSim, stopTrace, useSim } from "@/src/sim";
import { useTripStore } from "@/src/state/tripStore";
import { useTripEvents } from "@/src/data/profile";
import { savedAgeLabel, useSavedRouteAt } from "@/src/state/routeStore";
import { fonts, radius, shadow, spacing, useTheme } from "@/src/theme";
import type { RouteInfo } from "@/src/types";
import { api } from "@/src/api";

/** Replay speed clamps: a 65 mph highway trace, or the route's own average when it is slower. */
const MAX_MPS = 29;
const MIN_MPS = 8;
const routeMps = (r: RouteInfo) => Math.min(MAX_MPS, Math.max(MIN_MPS, r.duration_s > 0 ? r.distance_m / r.duration_s : MAX_MPS));

type Replay = { id: string; title: string; hint: string; icon: string; build: (route: RouteInfo) => TracePoint[] };

/**
 * Synthetic drives along the route the map screen has set, one fix per second. The "missed exit"
 * leaves the line 150 m to the right for 25 s just past the first exit/ramp maneuver (or a third
 * of the way when there is none): the tracker must call it off-route exactly once. The stop sits
 * at the half-way mark with speed 0 for 90 s: no reroute, no "tunnel" reset when it pulls out.
 */
const REPLAYS: Replay[] = [
  {
    id: "i80-clean",
    title: "I-80 clean",
    hint: "Drive the whole route on the line. Expect maneuvers only, no reroute.",
    icon: "road-variant",
    build: (r) => syntheticTrace(r.geometry, routeMps(r)),
  },
  {
    id: "i80-missed-exit",
    title: "I-80 missed exit",
    hint: 'Leave the line 150 m after the first exit. Expect one "Rerouting.".',
    icon: "call-missed",
    build: (r) => {
      const mps = routeMps(r);
      const cum = cumulativeMeters(r.geometry);
      const exit = (r.maneuvers ?? []).find((m) => m.action === "exit" || m.action === "ramp");
      const atM = exit ? cum[Math.min(exit.index, cum.length - 1)] + 2 * mps : cum[cum.length - 1] / 3;
      return syntheticTrace(r.geometry, mps, { detourAtIndex: Math.max(1, Math.round(atM / mps)), detourM: 150, detourPoints: 25 });
    },
  },
  {
    id: "truck-stop-stop",
    title: "Stop at a truck stop",
    hint: "Park half-way for 90 s, then carry on. Expect no reroute.",
    icon: "parking",
    build: (r) => {
      const mps = routeMps(r);
      const cum = cumulativeMeters(r.geometry);
      return syntheticTrace(r.geometry, mps, { stopAtIndex: Math.round(cum[cum.length - 1] / 2 / mps), stopS: 90 });
    },
  },
];

const KIND_ICON: Record<AnnouncementKind, string> = {
  maneuver: "arrow-decision",
  reroute: "directions-fork",
  advisory: "alert-outline",
  speed: "speedometer",
  system: "cog-outline",
  skipped: "volume-off",
};

const clock = (t: number) => {
  const d = new Date(t);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`;
};

const onlineLabel = (online: boolean | null) => (online === null ? "network unknown" : online ? "online" : "offline");

/** Tester screen (TESTER_CODE login only): jump the truck to made-up spots to try scenarios. */
export default function TesterScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const sim = useSim();
  const active = useTripStore((s) => s.active);
  const announcements = useAnnouncements();
  const savedAt = useSavedRouteAt();
  const { online } = useOnline();
  const [speedFactor, setSpeedFactor] = useState<1 | 4>(1);
  const playing = isTracePlaying();
  const trip = useTripLog();
  const rec = useRecorder();
  // Re-listed when a recording starts or a file lands (listTraces is a synchronous directory read).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const traces = useMemo(() => listTraces(), [rec.recording, rec.lastFile]);
  const [traceNote, setTraceNote] = useState<string | null>(null);
  const toggleRecording = async () => {
    if (rec.recording) {
      const name = await recorder.stop();
      setTraceNote(name ? `Saved ${name}` : null);
    } else {
      recorder.start({ tripId: active?.trip_id ?? null, place: active?.place ?? null, route: active?.route ?? null });
      setTraceNote(null);
    }
  };
  const share = async (name: string) => {
    setTraceNote((await shareTrace(name)) ? null : "Sharing is not available on this device; pull the file with adb.");
  };
  const replayRecorded = async (name: string) => {
    const trace = await readTrace(name);
    if (!trace) {
      setTraceNote(`Couldn't read ${name}.`);
      return;
    }
    if (!trace.fixes.length) {
      setTraceNote(`${name} has no fixes.`);
      return;
    }
    requestReplay({ trace, speedFactor, name });
    router.back();
  };
  // The trip log rows the backend holds for this drive, through react-query (the flush goes first, so the
  // rows include what the app just sent).
  const [tripAsked, setTripAsked] = useState(false);
  const tripQuery = useTripEvents(active?.trip_id, tripAsked);
  const tripRows = tripAsked ? (tripQuery.data ?? null) : null;
  const tripNote = !tripAsked ? null : tripQuery.isFetching ? "Loading…" : tripQuery.error ? `Couldn't load: ${String(tripQuery.error)}` : null;
  const loadTrip = async () => {
    if (!active?.trip_id) return;
    await tripLog.flush().catch(() => {});
    if (tripAsked) tripQuery.refetch();
    else setTripAsked(true); // enabling the query fetches it
  };
  // Drivers can reach this route by deep link (truckbuddy://tester); the simulated locations are for testers only.
  if (!isTester()) return <Redirect href="/" />;

  const startReplay = (r: Replay) => {
    if (!active) return;
    const trace = r.build(active.route);
    if (!trace.length) return;
    playTrace(trace, speedFactor, { id: `trace-${r.id}`, label: `Replay: ${r.title} (${speedFactor}x)` });
    router.back();
  };
  const traceMinutes = (r: Replay) => (active ? Math.max(1, Math.round(r.build(active.route).length / 60 / speedFactor)) : 0);

  return (
    <View style={[styles.root, { backgroundColor: colors.surfaceSecondary, paddingTop: insets.top }]}>
      <View style={styles.header}>
        <PressableScale testID="tester-back" accessibilityLabel="Back" onPress={() => router.back()} style={[styles.circle, { backgroundColor: colors.surface }]}>
          <Icon name="arrow-left" size={24} color={colors.onSurface} />
        </PressableScale>
        <Text style={[styles.title, { color: colors.onSurface }]}>Test scenarios</Text>
        <View style={styles.circle} />
      </View>

      <ScrollView contentContainerStyle={[styles.body, { paddingBottom: insets.bottom + spacing.xl }]}>
        <View style={[styles.status, { backgroundColor: colors.surface }]}>
          <Icon name={sim ? "map-marker-radius" : "crosshairs-gps"} size={22} color={sim ? colors.warning : colors.success} />
          <Text testID="tester-status" style={[styles.statusText, { color: colors.onSurface }]} numberOfLines={2}>
            {sim ? sim.label : "Using your real location"}
          </Text>
        </View>
        <View style={[styles.status, { backgroundColor: colors.surface }]}>
          <Icon name={online === false ? "signal-off" : "content-save-outline"} size={22} color={online === false ? colors.warning : colors.onSurfaceSecondary} />
          <Text testID="tester-offline-status" style={[styles.cardSub, { color: colors.onSurfaceSecondary, flex: 1 }]} numberOfLines={2}>
            Route on disk: {savedAgeLabel(savedAt)} · {onlineLabel(online)}
          </Text>
        </View>

        {SCENARIOS.map((s) => {
          const on = sim?.id === s.id;
          return (
            <PressableScale
              key={s.id}
              testID={`scenario-${s.id}`}
              accessibilityLabel={s.title}
              onPress={() => {
                setSim(s);
                router.back();
              }}
              style={[styles.card, shadow(colors), { backgroundColor: colors.surface, borderColor: on ? colors.brandPrimary : colors.surface }]}
            >
              <View style={[styles.icon, { backgroundColor: colors.surfaceSecondary }]}>
                <Icon name={s.icon} size={28} color={colors.onSurface} />
              </View>
              <View style={styles.cardText}>
                <Text style={[styles.cardTitle, { color: colors.onSurface }]}>{s.title}</Text>
                <Text style={[styles.cardSub, { color: colors.onSurfaceSecondary }]}>{s.hint}</Text>
              </View>
              <Icon name={on ? "check-circle" : "chevron-right"} size={24} color={on ? colors.brandPrimary : colors.muted} />
            </PressableScale>
          );
        })}

        {sim && (
          <PressableScale testID="tester-reset" accessibilityLabel="Back to real GPS" onPress={() => setSim(null)} style={[styles.reset, { borderColor: colors.onSurface }]}>
            <Icon name="crosshairs-gps" size={20} color={colors.onSurface} />
            <Text style={[styles.resetText, { color: colors.onSurface }]}>Back to real GPS</Text>
          </PressableScale>
        )}

        <View style={styles.sectionHead}>
          <Text style={[styles.section, { color: colors.onSurface }]}>Replay</Text>
          <View style={[styles.seg, { backgroundColor: colors.surfaceTertiary }]}>
            {([1, 4] as const).map((f) => (
              <PressableScale
                key={f}
                testID={`replay-speed-${f}`}
                accessibilityLabel={`${f}x speed`}
                onPress={() => setSpeedFactor(f)}
                style={[styles.segItem, speedFactor === f && { backgroundColor: colors.surface }]}
              >
                <Text style={[styles.segText, { color: speedFactor === f ? colors.onSurface : colors.muted }]}>{f}x</Text>
              </PressableScale>
            ))}
          </View>
        </View>
        <Text style={[styles.cardSub, { color: colors.onSurfaceSecondary }]}>
          {active
            ? `Drives the fake truck along the route to ${active.place.name}. Set the route, tap Go on the map, then pick a replay here.`
            : "Set a route on the map first (say “take me to the nearest truck stop”), tap Go, then come back here."}
        </Text>

        {REPLAYS.map((r) => {
          const on = sim?.id === `trace-${r.id}`;
          return (
            <PressableScale
              key={r.id}
              testID={`replay-${r.id}`}
              accessibilityLabel={r.title}
              disabled={!active}
              onPress={() => startReplay(r)}
              style={[styles.card, shadow(colors), { backgroundColor: colors.surface, borderColor: on ? colors.brandPrimary : colors.surface, opacity: active ? 1 : 0.5 }]}
            >
              <View style={[styles.icon, { backgroundColor: colors.surfaceSecondary }]}>
                <Icon name={r.icon} size={28} color={colors.onSurface} />
              </View>
              <View style={styles.cardText}>
                <Text style={[styles.cardTitle, { color: colors.onSurface }]}>{r.title}</Text>
                <Text style={[styles.cardSub, { color: colors.onSurfaceSecondary }]}>
                  {r.hint}
                  {active ? ` ~${traceMinutes(r)} min at ${speedFactor}x.` : ""}
                </Text>
              </View>
              <Icon name={on && playing ? "play-circle" : on ? "check-circle" : "chevron-right"} size={24} color={on ? colors.brandPrimary : colors.muted} />
            </PressableScale>
          );
        })}

        {playing && (
          <PressableScale testID="replay-stop" accessibilityLabel="Stop replay" onPress={stopTrace} style={[styles.reset, { borderColor: colors.onSurface }]}>
            <Icon name="stop-circle-outline" size={20} color={colors.onSurface} />
            <Text style={[styles.resetText, { color: colors.onSurface }]}>Stop replay (truck stays here)</Text>
          </PressableScale>
        )}

        <View style={styles.sectionHead}>
          <Text style={[styles.section, { color: colors.onSurface }]}>Recorded drives</Text>
        </View>
        <Text style={[styles.cardSub, { color: colors.onSurfaceSecondary }]}>
          Real GPS fixes with the route and what the copilot said, as a file you can share. A recording replays here on its own route.
        </Text>
        <PressableScale
          testID="trace-record"
          accessibilityLabel={rec.recording ? "Stop recording" : "Record this drive"}
          onPress={() => toggleRecording().catch(() => {})}
          style={[styles.reset, { borderColor: rec.recording ? colors.warning : colors.onSurface }]}
        >
          <Icon name={rec.recording ? "stop-circle-outline" : "record-circle-outline"} size={20} color={rec.recording ? colors.warning : colors.onSurface} />
          <Text style={[styles.resetText, { color: rec.recording ? colors.warning : colors.onSurface }]}>
            {rec.recording ? `Stop recording (${rec.fixes} fixes)` : "Record this drive"}
          </Text>
        </PressableScale>
        {traceNote && <Text style={[styles.cardSub, { color: colors.muted }]}>{traceNote}</Text>}
        {traces.length === 0 ? (
          <Text style={[styles.cardSub, { color: colors.muted }]}>No recordings yet.</Text>
        ) : (
          traces.slice(0, 10).map((name) => (
            <View key={name} style={[styles.status, { backgroundColor: colors.surface }]}>
              <Text style={[styles.cardSub, { color: colors.onSurface, flex: 1 }]} numberOfLines={2}>
                {name}
              </Text>
              <PressableScale testID={`trace-share-${name}`} accessibilityLabel={`Share ${name}`} onPress={() => share(name).catch(() => {})} style={styles.clear}>
                <Icon name="share-variant-outline" size={22} color={colors.info} />
              </PressableScale>
              <PressableScale testID={`trace-replay-${name}`} accessibilityLabel={`Replay ${name}`} onPress={() => replayRecorded(name).catch(() => {})} style={styles.clear}>
                <Icon name="play-circle-outline" size={24} color={colors.info} />
              </PressableScale>
            </View>
          ))
        )}

        <View style={styles.sectionHead}>
          <Text style={[styles.section, { color: colors.onSurface }]}>Trip log</Text>
          <PressableScale testID="trip-flush" accessibilityLabel="Flush trip events" onPress={() => tripLog.flush().catch(() => {})} style={styles.clear}>
            <Text style={[styles.segText, { color: colors.info }]}>Flush now</Text>
          </PressableScale>
        </View>
        <View style={[styles.status, { backgroundColor: colors.surface }]}>
          <Icon name="chart-timeline-variant" size={22} color={colors.onSurfaceSecondary} />
          <Text testID="trip-log-status" style={[styles.cardSub, { color: colors.onSurfaceSecondary, flex: 1 }]} numberOfLines={2}>
            {`${trip.pending} event${trip.pending === 1 ? "" : "s"} pending · last flush ${trip.lastFlushAt ? clock(trip.lastFlushAt) : "never"}`}
            {active?.trip_id ? ` · trip ${active.trip_id}` : " · no trip"}
          </Text>
        </View>
        {active?.trip_id && (
          <PressableScale testID="trip-load" accessibilityLabel="Load this trip's events" onPress={loadTrip} style={[styles.reset, { borderColor: colors.onSurface }]}>
            <Icon name="cloud-download-outline" size={20} color={colors.onSurface} />
            <Text style={[styles.resetText, { color: colors.onSurface }]}>Load this trip from the server</Text>
          </PressableScale>
        )}
        {(tripNote || tripRows) && (
          <View testID="trip-events" style={[styles.log, { backgroundColor: colors.surface }]}>
            {tripNote && <Text style={[styles.cardSub, { color: colors.muted }]}>{tripNote}</Text>}
            {tripRows?.length === 0 && <Text style={[styles.cardSub, { color: colors.muted }]}>Nothing stored for this trip yet.</Text>}
            {tripRows?.map((r) => (
              <View key={r.id} style={styles.logRow}>
                <Icon name={r.source === "server" ? "server-network" : "cellphone"} size={16} color={colors.onSurfaceSecondary} />
                <Text style={[styles.logTime, { color: colors.muted }]}>{clock(Date.parse(r.t))}</Text>
                <Text style={[styles.logText, { color: colors.onSurface }]}>
                  {r.kind} {Object.keys(r.data).length ? JSON.stringify(r.data) : ""}
                </Text>
              </View>
            ))}
          </View>
        )}

        <View style={styles.sectionHead}>
          <Text style={[styles.section, { color: colors.onSurface }]}>Announcements</Text>
          {announcements.length > 0 && (
            <PressableScale testID="announcements-clear" accessibilityLabel="Clear announcements" onPress={clearAnnouncements} style={styles.clear}>
              <Text style={[styles.segText, { color: colors.info }]}>Clear</Text>
            </PressableScale>
          )}
        </View>
        <View testID="announcement-log" style={[styles.log, { backgroundColor: colors.surface }]}>
          {announcements.length === 0 ? (
            <Text style={[styles.cardSub, { color: colors.muted }]}>Nothing spoken yet. Maneuvers, reroutes and skipped lines show up here (dev builds only).</Text>
          ) : (
            [...announcements].reverse().map((a, i) => (
              <View key={`${a.t}-${i}`} style={styles.logRow}>
                <Icon name={KIND_ICON[a.kind]} size={16} color={a.kind === "skipped" ? colors.muted : colors.onSurfaceSecondary} />
                <Text style={[styles.logTime, { color: colors.muted }]}>{clock(a.t)}</Text>
                <Text style={[styles.logText, { color: a.kind === "skipped" ? colors.muted : colors.onSurface }]}>{a.text}</Text>
              </View>
            ))
          )}
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: spacing.lg, paddingVertical: spacing.sm },
  circle: { width: 48, height: 48, borderRadius: 24, alignItems: "center", justifyContent: "center" },
  title: { fontFamily: fonts.bold, fontSize: 18 },
  body: { padding: spacing.lg, gap: spacing.md },
  status: { flexDirection: "row", alignItems: "center", gap: spacing.md, borderRadius: radius.lg, padding: spacing.lg },
  statusText: { flex: 1, fontFamily: fonts.semibold, fontSize: 15 },
  card: { flexDirection: "row", alignItems: "center", gap: spacing.md, borderRadius: radius.lg, padding: spacing.lg, borderWidth: 2 },
  icon: { width: 52, height: 52, borderRadius: 26, alignItems: "center", justifyContent: "center" },
  cardText: { flex: 1, gap: 2 },
  cardTitle: { fontFamily: fonts.bold, fontSize: 17 },
  cardSub: { fontFamily: fonts.regular, fontSize: 14 },
  reset: { height: 52, borderRadius: radius.pill, borderWidth: 1.5, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: spacing.sm, marginTop: spacing.sm },
  resetText: { fontFamily: fonts.semibold, fontSize: 16 },
  sectionHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: spacing.lg },
  section: { fontFamily: fonts.bold, fontSize: 20 },
  seg: { flexDirection: "row", borderRadius: radius.pill, padding: 3 },
  segItem: { paddingHorizontal: spacing.md, paddingVertical: 6, borderRadius: radius.pill },
  segText: { fontFamily: fonts.semibold, fontSize: 14 },
  clear: { paddingHorizontal: spacing.sm, paddingVertical: 4 },
  log: { borderRadius: radius.lg, padding: spacing.md, gap: spacing.xs },
  logRow: { flexDirection: "row", alignItems: "flex-start", gap: spacing.sm },
  logTime: { fontFamily: fonts.medium, fontSize: 12, marginTop: 2, fontVariant: ["tabular-nums"] },
  logText: { flex: 1, fontFamily: fonts.regular, fontSize: 14 },
});
