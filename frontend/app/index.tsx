import { useFocusEffect, useRouter } from "expo-router";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { AppState, BackHandler, Keyboard, Platform, StyleSheet, useWindowDimensions, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import * as Sentry from "@sentry/react-native";

import { absoluteUrl, api, isNoTruckRoute } from "@/src/api";
import { newSessionId, useCopilotTurn, type AnswerContext } from "@/src/dialog/useCopilotTurn";
import { useFillers } from "@/src/dialog/useFillers";
import { AlternativesSheet } from "@/src/components/AlternativesSheet";
import { CopilotSheet } from "@/src/components/CopilotSheet";
import { Icon } from "@/src/components/Icon";
import MapLayer from "@/src/components/MapLayer";
import type { MapLayerHandle } from "@/src/components/mapTypes";
import { MicPermissionCard } from "@/src/components/MicPermissionCard";
import { NavBanner } from "@/src/components/NavBanner";
import { PressableScale } from "@/src/components/PressableScale";
import { dial, ProvidersList } from "@/src/components/ProvidersList";
import { RestoredBanner } from "@/src/components/RestoredBanner";
import { CaptionBubble, Toast, type ToastData } from "@/src/components/Toast";
import { TopBar } from "@/src/components/TopBar";
import { MESSAGES } from "@/src/constants";
import { isTester } from "@/src/auth";
import { chainControlAhead, routePointsAhead } from "@/src/geo/ahead";
import { switchedLine } from "@/src/geo/routeChoice";
import { farFromRoute } from "@/src/geo/guidance";
import { createRerouteGate } from "@/src/geo/offroute";
import { useDriverLocation } from "@/src/hooks/useDriverLocation";
import { useGuidance, type Guidance } from "@/src/hooks/useGuidance";
import { useOfflineGuidance, type OfflineGuidance } from "@/src/hooks/useOfflineGuidance";
import { useOfflineMap } from "@/src/hooks/useOfflineMap";
import { isOnline, useOnline } from "@/src/hooks/useOnline";
import { useRouteAdvisories } from "@/src/hooks/useRouteAdvisories";
import { useSpeechQueue } from "@/src/hooks/useSpeechQueue";
import { saveProfile } from "@/src/data/profile";
import { getTruckProfile, isOnboarded, truckLabel, useTruckProfile } from "@/src/hooks/useTruckProfile";
import { useVoice, type MicPermission } from "@/src/hooks/useVoice";
import { WAKE_SUPPORTED } from "@/src/hooks/useWakeWord";
import { shadow, spacing, useTheme } from "@/src/theme";
import { providerPlace, restoreSavedTrip, selectPlaces, startTripPersistence, stopsAhead, useTripStore, viaOf } from "@/src/state/tripStore";
import { setHandsFree, setNudgesEnabled, setVoiceGuidance, takeTruckReroute, useHandsFree, useNudgesEnabled, useVoiceGuidance } from "@/src/state/settingsStore";
import { reduceAgentResponse } from "@/src/state/agentReducer";
import { applyEffects } from "@/src/state/applyEffects";
import { offlineTurn } from "@/src/state/offlineTurn";
import type { ActiveRoute, AgentResponse, LatLng, ParkingCheck, Phase, Place, RouteInfo } from "@/src/types";
import { backAction } from "@/src/utils/backAction";
import { PRIORITY } from "@/src/voice/queue";
import { streetFromInstruction } from "@/src/voice/sttHint";
import { entrySpot, routeEndsAt } from "@/src/utils/entry";
import { parkingFollowup, toAudio } from "@/src/utils/parkingFollowup";
import { RECORD_DRIVES, recorder } from "@/src/geo/driveRecorder";
import { subscribeReplay, takeReplayRequest } from "@/src/geo/replayRequest";
import { startTripLog, stopTripLog, tripLog } from "@/src/net/tripEvents";
import { playTrace } from "@/src/sim";

/** Toast for a failed /route: the router's "no truck route" is a destination problem, not a retry. */
function routeFailText(e: unknown): string {
  return isNoTruckRoute(e) ? "No truck route there from here. Try another destination or an address on a main road." : "Couldn't build a route. Try again.";
}

// Below this much visible map (layout units) the bubble would cover the route; the sheet shows the text.
// The route card leaves about 220 at the default font, about 120 at 130 %.
const MIN_MAP_FOR_BUBBLE = 180;
type Caption = { reply: string; heard?: string; kind?: "reply" | "line"; at?: number };
/** The copilot's answer: kept until the next turn (a driver who looks later must still see it). */
const replyCaption = (reply: string, heard?: string): Caption => ({ reply, heard, kind: "reply", at: Date.now() });
/** A spoken line (turn, nudge, parking tip): gone 8 s after it is said, and never over an answer under 6 s old. */
const lineCaption = (text: string, prev: Caption | null): Caption =>
  prev?.kind === "reply" && Date.now() - (prev.at ?? 0) < 6000 ? prev : { reply: text, kind: "line", at: Date.now() };

const RESEARCH_INTERIM = "Reading reviews now, almost there.";
const RESEARCH_INTERIM_MS = 7000;

const ROUTE_STAGE = "Setting route…";
// Nudges (/api/nudges): first ask a minute into the drive, then every five minutes; a nudge that
// is still waiting when the next one comes is stale. After one plays the mic opens for ~7 s.
const NUDGE_FIRST_MS = 60 * 1000;
const NUDGE_EVERY_MS = 5 * 60 * 1000;
const NUDGE_KEY_PREFIX = "nudge-";
/** " There's a lot with trailers to the southeast, looks like a private yard." (or "") */
const APPROACH_TIPS_M = 800; // about half a mile: the entry tip is useful here, not six miles out
/** One short line about the lot right after the answer; "" when there is nothing certain to say. */
const statusLine = (pk: ParkingCheck) => {
  if (pk.verdict === "likely" || pk.verdict === "listed") return "Parking looks good there.";
  return "";
};
/** The entry and parking tips as one spoken line, for the approach and for "how do I get in". */
const tipsLine = (pk: ParkingCheck) => {
  const entry = pk.entry_tip && pk.relative ? `The truck entrance is ${pk.entry_tip.charAt(0).toLowerCase()}${pk.entry_tip.slice(1)}` : pk.entry_tip;
  const tips = [entry, pk.park_tip].filter(Boolean).join(". ");
  return tips ? `${tips}.` : "";
};
const nearbyLine = (pk: ParkingCheck) => {
  if (!pk.nearby) return "";
  const look = pk.nearby.kind === "unclear" ? "" : `, looks like a ${pk.nearby.kind}`;
  return ` There's a lot with trailers to the ${pk.nearby.direction}${look}.`;
};
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
  const { height: windowH } = useWindowDimensions();
  const mapWindow = windowH - topH - sheetH; // the map the chrome leaves visible; small at 130 % font
  // The sheet's peek height while the driver has pulled it down; the map keeps sheetH as its inset
  // (no camera refit on a drag) but the chrome above the sheet moves down into the freed room.
  const [peekH, setPeekH] = useState<number | null>(null);
  const chromeBottom = (peekH ?? sheetH) + spacing.lg;
  // The trip (route, driving, stops, options, providers, parking, sheet) lives in src/state/tripStore.ts;
  // async handlers read `trip()` at the moment they need a value instead of a ref mirror of a render.
  const trip = useTripStore.getState;
  const active = useTripStore((s) => s.active);
  const driving = useTripStore((s) => s.driving);
  const options = useTripStore((s) => s.options);
  const providers = useTripStore((s) => s.providers);
  const parking = useTripStore((s) => s.parking);
  const sheet = useTripStore((s) => s.sheet);
  const restoredId = useTripStore((s) => s.restoredId);
  const places = useTripStore(selectPlaces);
  // The dialog's own phases (listening, thinking) sit on top of the sheet.
  const [dialogPhase, setDialogPhase] = useState<"listening" | "thinking" | null>(null);
  const phase: Phase = dialogPhase ?? sheet;
  const phaseRef = useRef<Phase>(phase);
  useLayoutEffect(() => {
    phaseRef.current = phase;
  }, [phase]);
  const setPhase = (p: Phase) => {
    if (p === "listening" || p === "thinking") setDialogPhase(p);
    else {
      setDialogPhase(null);
      trip().setSheet(p);
    }
  };
  const [toast, setToast] = useState<ToastData | null>(null);
  // kind "reply" = the copilot's answer, kept until the next turn (a driver who looks later must still
  // see it); kind "line" = a spoken line (turn, nudge, parking tip), gone 8 s after it is said.
  const [caption, setCaption] = useState<Caption | null>(null);
  /** A spoken line's caption; it never covers an answer that is less than 6 s old. */
  const showLine = (text: string) => setCaption((prev) => lineCaption(text, prev));
  const [micCard, setMicCard] = useState<Exclude<MicPermission, "granted"> | null>(null);
  const [fit, setFit] = useState<{ key: string; points: LatLng[] }>({ key: "init", points: [] });
  const [thinkingText, setThinkingText] = useState<string | undefined>(undefined);
  const setDriving = (v: boolean) => trip().setDriving(v);
  const [overview, setOverview] = useState(false);
  // The list of the other routes the engine offered (the "Routes (N)" chip, before "Go").
  const [routesOpen, setRoutesOpen] = useState(false);
  // "Follow" button: the camera keeps the truck arrow in view until the driver drags the map.
  const [tracking, setTracking] = useState(false);
  const following = tracking || (driving && !overview);
  // A new view to show (route, options, overview) wins over the Follow button.
  const [fitSeen, setFitSeen] = useState(fit.key);
  if (fitSeen !== fit.key) {
    setFitSeen(fit.key);
    if (fit.key !== "init" && fit.key !== "gps") setTracking(false);
  }
  // Whole-route view while driving: back to following the truck after 15 s.
  useEffect(() => {
    if (!overview) return;
    const t = setTimeout(() => setOverview(false), 15000);
    return () => clearTimeout(t);
  }, [overview]);
  const voiceOn = useVoiceGuidance();
  const setVoiceOn = setVoiceGuidance;
  const { online } = useOnline();

  const searchBlurGuard = useRef(false); // while set, "Where to?" gives a focus it gets back (see onSubmitText)

  const showToast = useCallback((text: string, kind: ToastData["kind"] = "error") => {
    setToast({ id: Date.now(), text, kind });
  }, []);

  // Everything the copilot says on its own (maneuvers, reroutes, reminders) goes through one
  // queue: it waits while the driver is talking to the copilot or a reply plays (never cuts it
  // off), and orders by urgency. The caption follows whatever line is on air. A nudge ("Want me
  // to add a stop at Pilot Fernley?") opens the mic for a few seconds once it has played:
  // listenForAnswer, defined further down (it needs the wake word and the mic), through this ref.
  const nudgeAnswer = useRef<() => void>(() => {});
  const { say, clear: clearSpeech } = useSpeechQueue(
    voice,
    () => phaseRef.current === "listening" || phaseRef.current === "thinking",
    (item) => {
      showLine(item.text);
      recorder.mark("said", item.text);
    },
    (item) => {
      if (item.key?.startsWith(NUDGE_KEY_PREFIX)) nudgeAnswer.current();
    },
    (item, why) => tripLog.log("speech_dropped", { key: item.key ?? null, priority: item.priority, kind: item.kind, why }, trip().active?.trip_id),
  );
  // The trip log mirrors its backlog to storage and flushes once a minute while the screen lives; the
  // drive recorder marks every logged event on its timeline.
  useEffect(() => {
    startTripLog();
    const off = tripLog.onEvent((e) => recorder.mark(e.kind, JSON.stringify(e.data)));
    return () => {
      off();
      stopTripLog();
    };
  }, []);
  // The recorder follows the active route: a reroute, an alternative or a stop lands on its timeline.
  useEffect(() => {
    if (active?.route) recorder.routeChanged(active.route);
  }, [active?.route]);
  // A build for a day on the road records every drive from Go to End route.
  useEffect(() => {
    if (RECORD_DRIVES && driving && active && !recorder.isRecording()) {
      recorder.start({ tripId: active.trip_id ?? null, place: active.place, route: active.route });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [driving]);
  // The tester picked a recorded drive: its own route goes on the map, then its fixes drive the truck.
  useEffect(() => {
    const apply = () => {
      const req = takeReplayRequest();
      if (!req) return;
      const { trace, speedFactor, name } = req;
      if (trace.place && trace.route) {
        clearSpeech();
        trip().startRoute({ place: trace.place, route: trace.route, trip_id: trace.trip_id ?? undefined });
        setDialogPhase(null);
        setDriving(true);
      }
      playTrace(trace.fixes, speedFactor, { id: `trace-${name}`, label: `Replay: ${name} (${speedFactor}x)` });
    };
    apply();
    return subscribeReplay(apply);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 3200);
    return () => clearTimeout(t);
  }, [toast]);

  useEffect(() => {
    if (!caption || voice.speaking || caption.kind === "reply") return; // answers stay until the next turn
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
  useEffect(() => {
    if (!active || !truck.break_hours) return;
    const limitMs = truck.break_hours * 3600 * 1000;
    const t = setInterval(() => {
      const start = trip().routeStart;
      if (!start || Date.now() - start < limitMs) return;
      useTripStore.setState({ routeStart: Date.now() }); // remind again after the next interval
      const who = truck.driver_name ? `Hey ${truck.driver_name}, ` : "";
      const hrs = truck.break_hours === 1 ? "an hour" : `${truck.break_hours} hours`;
      const text = `${who}you've been driving for ${hrs}. Want me to find a rest area?`;
      say(text, { priority: PRIORITY.WEATHER, kind: "ambient", key: "break", ttlMs: 10 * 60 * 1000 });
    }, 60000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, truck.break_hours, truck.driver_name]);

  // Re-center once when the real GPS fix arrives. (Nothing reads `center` back; the map only reports it.)
  const [fixSeen, setFixSeen] = useState<boolean | null>(null);
  if (fixSeen !== loc.isDemo) {
    setFixSeen(loc.isDemo);
    if (!loc.isDemo) setFit({ key: "gps", points: [loc.coords] });
  }

  // Tester jumped to a made-up spot (or back to GPS): show it.
  const simId = loc.sim?.id ?? "real";
  const firstSim = useRef(true);
  useEffect(() => {
    if (firstSim.current) {
      firstSim.current = false;
      return;
    }
    center.current = loc.coords;
    setFit({ key: `sim-${simId}-${Date.now()}`, points: [loc.coords] });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [simId]);

  // Fit all option pins once the (taller) options sheet has been laid out. The map refits only
  // when the key changes, so the key is all that has to be tracked.
  const optionsFit = phase === "options" && options.length ? `options-${sheetH}-${options.length}` : null;
  const [optionsFitSeen, setOptionsFitSeen] = useState(optionsFit);
  if (optionsFit !== optionsFitSeen) {
    setOptionsFitSeen(optionsFit);
    if (optionsFit) setFit({ key: optionsFit, points: [loc.coords, ...options] });
  }

  const sayReply = (res: AgentResponse, isCurrent: () => boolean = () => true) => {
    setCaption(replyCaption(res.reply, res.transcript || undefined));
    if (!res.audio_url) return Promise.resolve();
    // The playback timeouts are sized from the text behind each clip: the server says how it split
    // the reply when it can; older backends leave that to the sentence split.
    const parts = res.reply_parts?.length ? res.reply_parts : res.reply.split(/(?<=[.!?])\s+/);
    const [first, ...restParts] = parts;
    const rest = restParts.join(" ");
    const done = voice.playAndWait(absoluteUrl(res.audio_url), Math.min(20000, 1500 + first.length * 65));
    if (!res.audio_url_2) return done;
    // Buffer the second half while the first plays, so it starts right after it (<150 ms gap).
    const url2 = absoluteUrl(res.audio_url_2);
    const next = voice.preload(url2);
    const available = fetch(url2, { headers: { Range: "bytes=0-0" } })
      .then((r) => r.ok)
      .catch(() => false);
    return done.then(async () => {
      if (!isCurrent()) {
        next.remove();
        return;
      }
      if (!(await available)) {
        console.warn(`[voice] second half unavailable (${url2}); showing the full text instead`);
        next.remove();
        setCaption(replyCaption(res.reply, res.transcript || undefined));
        return;
      }
      if (!isCurrent()) {
        next.remove();
        return;
      }
      return voice.playAndWait(next, Math.min(20000, 1500 + (rest || res.reply).length * 65));
    });
  };

  const fillers = useFillers(voice.playAndWait);

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

  // The first map is not empty: while nothing is on it (no route, no search, no pins yet) the truck stops
  // around the driver go on as pins, a tap routes there, and the camera widens once to show the nearest
  // three. They make way for any search or route and come back when the map is empty again (QA 10-28, U4).
  const startPinsEmptyAt = useRef<LatLng | null>(null); // where a fetch found nothing: no retry at the same spot
  useEffect(() => {
    if (active || phase !== "ready" || options.length || providers || places.length) return;
    if (loc.isDemo && !loc.resolved) return;
    const e = startPinsEmptyAt.current;
    if (e && Math.hypot(e.lat - loc.coords.lat, (e.lng - loc.coords.lng) * 0.8) < 0.045) return;
    const at = loc.coords;
    let cancelled = false;
    api
      .places("truck_stop", at)
      .then((res) => {
        const t = trip();
        if (cancelled || t.active || phaseRef.current !== "ready" || t.options.length) return;
        if (!res.places.length) {
          startPinsEmptyAt.current = at;
          return;
        }
        const nearest = res.places.slice(0, 8);
        t.setStartPins(nearest);
        setFit({ key: `start-pins-${at.lat.toFixed(3)},${at.lng.toFixed(3)}`, points: [at, ...nearest.slice(0, 3)] });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [active, phase, options.length, providers, places.length, loc.coords, loc.isDemo, loc.resolved]);

  /** Satellite parking check runs after the route is set; its directions are spoken when the reply ends. */
  const followParking = async (place: Place, replyDone: Promise<void>, isCurrent: () => boolean, approach = trip().active?.route.approach) => {
    if (place.category === "service") return; // breakdown provider: no truck-parking check
    try {
      const pk = await api.parking(place, approach);
      // The card always takes the result if it's still the same destination.
      trip().setActiveParking(place.id, pk);
      const e = entrySpot(pk);
      const cur = trip().active;
      if (e && cur?.place.id === place.id && !routeEndsAt(cur.route, e)) {
        // Route to E. An off-route reroute landing meanwhile makes this plan stale and it is dropped; one
        // more try against the route that replaced it, so the truck is still guided to the entrance.
        reroute("entrance").then((applied) => {
          const now = trip().active;
          if (!applied && now?.place.id === place.id && !routeEndsAt(now.route, e)) reroute("entrance");
        });
      }
      if (!isCurrent() || trip().active?.place.id !== place.id) return;
      // Spoken right after the answer: the verdict only. The entry and parking tips stay on the card and
      // are said when the truck is within half a mile (approachTips below) or on "how do I get in".
      // The whole set after every answer ran 17 s (QA 2026-10-28, U5).
      const text = pk.verdict === "unlikely" ? `Heads up: from the satellite, parking there looks tight for a rig.${nearbyLine(pk)}` : statusLine(pk);
      if (!text) return;
      const audio = api.speak(text); // generate while the first answer is still playing
      await replyDone;
      if (!isCurrent() || trip().active?.place.id !== place.id) return;
      showLine(text);
      const r = await audio;
      if (isCurrent() && trip().active?.place.id === place.id) {
        await voice.playAndWait(absoluteUrl(r.audio_url), Math.min(15000, 1500 + text.length * 65));
      }
    } catch {
      // parking stays "unclear"; the route still works
    }
  };

  const startRoute = (next: ActiveRoute) => {
    const tripped = trip().startRoute(next);
    tripLog.log("route_started", {
      place_id: next.place.id, category: next.place.category, distance_m: next.route.distance_m, duration_s: next.route.duration_s,
      alternatives: next.route.alternatives?.length ?? 0, restricted: next.route.restricted ?? null, source: next.route.source,
    }, tripped.trip_id);
    setDialogPhase(null);
    // Not while driving: the follow camera stays on the truck (the whole-route fit flashed for a second
    // and snapped back on the next fix).
    if (!trip().driving) setFit({ key: `route-${next.place.id}-${Date.now()}`, points: [loc.coords, next.place] });
  };

  const backToIdle = () => setPhase(trip().idleSheet());

  // The disk copy of the trip survives a restart (src/state/tripStore.ts writes every change); every Sentry
  // event of a drive carries its trip id, so a crash can be put next to the trip log.
  useEffect(() => {
    Sentry.setTag("trip_id", active?.trip_id ?? "none");
  }, [active?.trip_id]);
  useEffect(() => startTripPersistence(), []);

  // A route saved before the last restart (crash, dead zone, phone off) comes back silently, not
  // driving, with a banner: Resume drives it, Dismiss forgets it.
  useEffect(() => {
    restoreSavedTrip().then((r) => {
      if (!r) return;
      const { restored: res, saved } = r;
      tripLog.log("route_restored", { place_id: res.place.id, saved_age_s: Math.round((Date.now() - saved.savedAt) / 1000), was_driving: saved.driving === true }, res.trip_id);
      setDialogPhase(null);
      // The whole saved line: the GPS fix is not in yet, and the demo point would span half the country.
      const line = res.route.geometry.filter((_, i) => i % 25 === 0).map(([lat, lng]) => ({ lat, lng }));
      setFit({ key: `restored-${res.place.id}`, points: [...line, res.place] });
    });
  }, []);
  const restored = restoredId !== null && active?.place.id === restoredId && !driving;
  useEffect(() => {
    // Nothing pressed in 20 s: the banner goes, the route stays in preview (same as "Keep"). Not when the truck
    // was driving when the app died: then Resume is what the driver came back for, and it stays until pressed.
    if (!restored || trip().restoredDriving) return;
    const t = setTimeout(() => trip().dismissRestored(), 20000);
    return () => clearTimeout(t);
  }, [restored]);

  /**
   * The tracker says the truck left the route: ask the engine to resume the plan from here
   * (/reroute with the handle, section and metres into it), or plan afresh when that fails.
   * One at a time; the new route only lands if the destination is still the same.
   * Not for a tester parked on a fake spot (no trace = nothing moves) and not before "Go".
   */
  /**
   * A route that landed while driving and breaks a truck restriction (or needs a permit) is said out loud:
   * the card with the red banner is not on screen then, and the turn calls would otherwise just carry on.
   */
  const warnRestricted = (route: RouteInfo) => {
    if (!route.restricted || !trip().driving) return;
    const line = route.notices?.[0] ?? "";
    const text =
      route.restricted === "legal"
        ? `Heads up: ${line || "this route needs a permit for your truck."}`
        : `Heads up, the new route isn't cleared for your truck. ${line} Watch the signs.`;
    say(text.replace(/\s+/g, " ").trim(), { priority: PRIORITY.MANEUVER, kind: "system", key: "route-restricted", ttlMs: 30000 });
  };

  const rerouting = useRef(false);
  const rerouteGate = useRef(createRerouteGate()).current;
  const offlineRef = useRef<OfflineGuidance | null>(null);
  const rerouteOffRoute = async (g: Guidance) => {
    const cur = trip().active;
    if (!cur || rerouting.current || !trip().driving) return;
    if (loc.sim && !loc.sim.trace) return;
    if (!rerouteGate.allow(Date.now())) return;
    // No signal: no /reroute. Point the driver back at the loaded line instead; the reroute
    // runs as soon as the signal is back (useOfflineGuidance calls this again).
    if (!isOnline()) {
      offlineRef.current?.onOffRoute(g);
      return;
    }
    rerouting.current = true;
    const route = cur.route;
    const starts = route.section_starts ?? [];
    let sectionIndex = 0;
    for (let i = 0; i < starts.length; i++) if (starts[i] <= g.matchedIndex) sectionIndex = i;
    const sectionStart = starts[sectionIndex] ?? 0;
    const traveledM = Math.max(0, g.alongM - (g.cum[sectionStart] ?? 0));
    say("Rerouting.", { priority: PRIORITY.MANEUVER, kind: "system", key: "reroute", ttlMs: 20000 });
    const gen = trip().routeGen;
    const event = { cause: "off_route", dist_m: Math.round(g.distM), threshold_m: g.thresholdM, handle: !!route.handle, section_index: sectionIndex };
    // /reroute gets the whole via list and the section index and drops the stops behind on its side; the
    // fallback /route and the stop chips only get what is still ahead.
    const ahead = cur.stops?.length ? cur.stops.slice(Math.min(sectionIndex, cur.stops.length)) : cur.stops;
    let fallback = false;
    try {
      let res;
      try {
        res = await api.reroute({
          lat: loc.coords.lat,
          lng: loc.coords.lng,
          place: cur.place,
          handle: route.handle ?? null,
          heading: loc.heading ?? null,
          section_index: sectionIndex,
          traveled_m: Math.round(traveledM),
          via: route.via ?? [],
          trip_id: cur.trip_id ?? null,
        });
      } catch (e) {
        console.log(`[reroute] /reroute failed (${String(e)}), planning from scratch`);
        fallback = true;
        res = await api.route(loc.coords, cur.place, false, (route.via ?? []).slice(sectionIndex), cur.trip_id ?? null);
      }
      // Only onto the route the truck left: a stop added or a route picked while this ran is newer.
      const applied = trip().applyRoute(gen, res.route, ahead, res.parking);
      if (applied) warnRestricted(res.route);
      tripLog.log("reroute", { ...event, ok: true, fallback, applied, restricted: res.route.restricted ?? null }, cur.trip_id);
      g.resetOffRoute();
    } catch {
      tripLog.log("reroute", { ...event, ok: false, fallback }, cur.trip_id);
      showToast("Couldn't reroute. Follow the map for now and watch the truck signs.");
    } finally {
      rerouting.current = false;
      rerouteGate.done(Date.now());
    }
  };
  // One object per GPS fix (not per render): useGuidance pushes each fix into the off-route tracker once.
  const fix = useMemo(
    () => ({ ...loc.coords, speed: loc.speed, accuracy: loc.accuracy, ts: loc.ts }),
    [loc.coords, loc.speed, loc.accuracy, loc.ts],
  );
  const guidance = useGuidance(active, fix, driving, voiceOn, say, rerouteOffRoute, (next, stage, toNextM) => {
    // The "now" call is the one that can come late: under 60 m at 65 mph is less than two seconds.
    if (stage === "now") tripLog.log("maneuver_said", { action: next.action, index: next.index, to_next_m: Math.round(toNextM) }, trip().active?.trip_id);
  });

  // Half a mile out, once per destination: the entry and parking tips from the card, through the speech
  // queue (a due turn still comes first).
  const tipsSaidFor = useRef<string | null>(null);
  useEffect(() => {
    if (!driving || !active?.parking || guidance.remainingM == null || guidance.remainingM > APPROACH_TIPS_M) return;
    if (tipsSaidFor.current === active.place.id) return;
    const text = tipsLine(active.parking);
    if (!text) return;
    tipsSaidFor.current = active.place.id;
    say(text, { priority: PRIORITY.CLOSURE_CHAIN, kind: "system", key: "approach-tips", ttlMs: 5 * 60 * 1000 });
  }, [driving, active, guidance.remainingM, say]);

  // Lost signal: "I'll keep you on the loaded route", and where the line is when off it.
  const driverPos = useMemo(() => ({ ...loc.coords, heading: loc.heading }), [loc.coords, loc.heading]);
  const offline = useOfflineGuidance(active, online, driving, guidance, driverPos, say, rerouteOffRoute);
  useEffect(() => {
    offlineRef.current = offline;
  }, [offline]);
  // Offline corridor pack for the route (MapLibre builds; null with the Apple provider).
  const offlineMap = useOfflineMap(active?.route ?? null);
  // Closures, weather, chains and speed alerts while driving; the shield and chips on the banner.
  const advisories = useRouteAdvisories(driving ? active : null, guidance, driving ? loc.speed : null, say);
  const limitRef = useRef<number | null>(null);
  useEffect(() => {
    limitRef.current = advisories.limitMph;
  }, [advisories.limitMph]);
  const guidanceRef = useRef<Guidance>(guidance);
  useEffect(() => {
    guidanceRef.current = guidance;
  }, [guidance]);
  const coordsRef = useRef<LatLng>(loc.coords);
  useEffect(() => {
    coordsRef.current = loc.coords;
  }, [loc.coords]);

  // ---- The dialog: src/dialog/turnMachine.ts decides, useCopilotTurn runs the commands (listen, STT, the agent
  // call, the mic reopening), this screen supplies what they need and applies each answer. Everything read
  // after an await comes through refs or getters, never from the render that started the turn. ----
  const handsFree = useHandsFree();
  const handsFreeRef = useRef(handsFree);
  const voiceOnRef = useRef(voiceOn);
  const locRef = useRef(loc);
  const captionRef = useRef(caption);
  useLayoutEffect(() => {
    handsFreeRef.current = handsFree;
    voiceOnRef.current = voiceOn;
    locRef.current = loc;
    captionRef.current = caption;
  });
  const { turn } = useCopilotTurn({
    voice: { listen: (o) => voice.listen(o), cancelRecording: voice.cancelRecording, stopSpeaking: voice.stopSpeaking },
    api: { transcribe: api.transcribe, agentSpeak: api.agentSpeak },
    fillers,
    offline: () => offlineTurn({ isOnline, say, setCaption }),
    context: () => {
      const t = trip();
      const cur = t.active;
      return {
        coords: coordsRef.current, // the truck moved while the driver talked and the words were transcribed
        active: cur,
        ui: { driving: t.driving, voice_guidance: voiceOnRef.current, hands_free: handsFreeRef.current && WAKE_SUPPORTED, options_count: t.options.length },
        heading: locRef.current.heading ?? null,
        speed: locRef.current.speed ?? null,
        limitMph: limitRef.current,
        // Names the recogniser should expect: what is on screen and on the route, most useful first.
        hintNames: [
          cur?.place.name,
          ...t.options.map((p) => p.name),
          ...t.places().map((p) => p.name),
          ...(cur?.stops ?? []).map((s) => s.name),
          ...(cur?.route.labels ?? []),
          streetFromInstruction(guidanceRef.current.next?.instruction),
        ],
      };
    },
    onPhase: setPhase,
    onIdle: () => backToIdle(),
    onThinking: setThinkingText,
    onHeard: (transcript) => setCaption({ reply: "…", heard: transcript }),
    onNotUnderstood: () => {
      if (!captionRef.current) setCaption({ reply: MESSAGES.notUnderstood });
    },
    onError: (message) => setCaption(replyCaption(message)),
    onListenFailed: () => showToast(MESSAGES.notUnderstood),
    onResponse: (res, ctx) => applyResponse(res, ctx),
  });
  // A nudge just played: a short mic window for the "yes" (the speech queue's onDone, registered above).
  useEffect(() => {
    nudgeAnswer.current = turn.nudgeOffer;
  });

  // ---- Nudges: while driving, every few minutes the backend may have something to offer (a
  // break coming due, parking ahead). Each line is spoken as ambient; once it has played the mic
  // opens for a few seconds (listenForAnswer) and the backend keeps the offer pending for the next
  // turn of the same session, so "yes" is enough. Not offline, not before "Go". ----
  const hasRoute = !!active;
  const nudgesOn = useNudgesEnabled();
  useEffect(() => {
    if (!driving || !hasRoute || !nudgesOn) return;
    const ctrl = new AbortController();
    let every: ReturnType<typeof setInterval> | null = null;
    const poll = async () => {
      if (!isOnline()) return;
      const cur = trip().active;
      if (!cur) return;
      const g = guidanceRef.current;
      // The offer must land in the session the answer will use: a live one, else one started here.
      const before = turn.dialogId();
      const sessionId = turn.isStale() || !before ? newSessionId() : before;
      const since = trip().drivingSince;
      try {
        const { nudges } = await api.nudges(
          {
            lat: coordsRef.current.lat,
            lng: coordsRef.current.lng,
            driving: true,
            active_place: cur.place,
            ahead: routePointsAhead(cur.route.geometry, g.cum, g.matchedIndex, g.alongM),
            chain_control_ahead: chainControlAhead(cur.route.chain_controls, g.cum, g.matchedIndex, g.alongM) ?? undefined,
            drive_elapsed_min: since === null ? null : Math.min(24 * 60, Math.max(0, Math.round((Date.now() - since) / 60000))),
            local_hour: new Date().getHours(),
            session_id: sessionId,
          },
          ctrl.signal,
        );
        if (ctrl.signal.aborted || !nudges.length) return;
        turn.adoptSession(sessionId); // the answer must not start a fresh session
        for (const n of nudges) {
          say(n.text, { priority: PRIORITY.WEATHER, kind: "ambient", key: `${NUDGE_KEY_PREFIX}${n.kind}`, ttlMs: NUDGE_EVERY_MS });
        }
      } catch (e) {
        if (!ctrl.signal.aborted) console.log(`[nudges] ${String(e)}`);
      }
    };
    const first = setTimeout(() => {
      poll();
      every = setInterval(poll, NUDGE_EVERY_MS);
    }, NUDGE_FIRST_MS);
    return () => {
      clearTimeout(first);
      if (every) clearInterval(every);
      ctrl.abort();
    };
  }, [driving, hasRoute, nudgesOn, say, turn]); // refs and a stable callback; `active` itself must not restart the timer

  const onGo = () => {
    turn.pause();
    voice.stopSpeaking();
    setCaption(null);
    setRoutesOpen(false);
    setDriving(true);
  };

  /** Typed request: same pipeline and session as voice, without recording or reopening the mic.
   *  Sent while the copilot is still on the last request, it replaces that request (barge-in). */
  const onSubmitText = (text: string): boolean => {
    Keyboard.dismiss();
    // Android hands the focus to the next TextInput when the focused one is cleared or unmounts: the
    // "Where to?" box took it after every typed request and kept the keyboard over the answer.
    searchBlurGuard.current = true;
    setTimeout(() => (searchBlurGuard.current = false), 1500);
    voice.stopSpeaking();
    setCaption(null);
    turn.text(text);
    return true;
  };

  /** Breakdown: say "give me a minute", wait for the research, an interim line at ~7 s, then the list. */
  const runResearch = async (res: AgentResponse, ctx: AnswerContext): Promise<{ spoken: Promise<void> }> => {
    setThinkingText("Checking who's around…");
    const intro = sayReply(res, ctx.isCurrent);
    let done = false;
    const interimAudio = api.speak(RESEARCH_INTERIM).catch(() => null);
    let interimPlayed: Promise<unknown> = Promise.resolve();
    const interim = setTimeout(() => {
      if (done || !ctx.isCurrent()) return;
      setThinkingText("Reading reviews…");
      interimPlayed = intro.then(async () => {
        const a = await interimAudio;
        if (a && !done && ctx.isCurrent()) await voice.playAndWait(absoluteUrl(a.audio_url), 4000);
      });
    }, RESEARCH_INTERIM_MS);
    let out: AgentResponse;
    try {
      out = await api.breakdownResult(res.research_id!, ctx.signal);
    } finally {
      done = true; // a failed research must not play "Reading reviews" afterwards
      clearTimeout(interim);
    }
    if (!ctx.isCurrent()) return { spoken: Promise.resolve() };
    await intro;
    await interimPlayed;
    if (!ctx.isCurrent()) return { spoken: Promise.resolve() };
    if (out.status === "providers" && out.providers?.length) {
      const list = out.providers;
      trip().setProviders({ providers: list, call_card: out.call_card, summary: out.summary, provider_source: out.provider_source });
      setPhase("providers");
      setFit({ key: `providers-${ctx.turnId}`, points: [loc.coords, ...list] }); // one research per turn: unique
    } else backToIdle();
    turn.answered(out.status, !!out.end_conversation); // the loop follows the final answer, not the "give me a minute"
    return { spoken: sayReply(out, ctx.isCurrent) };
  };

  // Modal screens (onboarding, truck profile): a return re-plans with a changed truck (below).
  const [focused, setFocused] = useState(true);
  useFocusEffect(
    useCallback(() => {
      setFocused(true);
      return () => setFocused(false);
    }, []),
  );

  const onAvatarPress = async () => {
    if (phase === "listening") {
      voice.finishListening();
      return;
    }
    // Barge-in: a tap while the copilot is still working drops that turn and listens again (same session,
    // so "no, the other one" lands where it should); useCopilotTurn does the dropping.
    voice.stopSpeaking();
    const perm = await voice.micPermission();
    if (perm !== "granted") {
      setMicCard(perm);
      return;
    }
    if (!turn.dialogId()) setCaption(null);
    turn.tap();
  };

  const onAllowMic = async () => {
    // A request that never answers (seen in Expo Go) must not leave the card dead: after 5 s it is
    // treated as blocked, and the card offers Settings.
    const perm = await Promise.race([
      voice.requestMic(),
      new Promise<MicPermission>((r) => setTimeout(() => r("blocked"), 5000)),
    ]);
    if (perm === "granted") {
      setMicCard(null);
      turn.tap();
    } else {
      // Asked once with context; after that only Settings can fix it.
      setMicCard("blocked");
    }
  };

  const onOptionParking = async (p: Place) => {
    const setParking = trip().setParking;
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
    turn.pause();
    turn.cancel(); // discard any in-flight turn
    voice.cancelRecording().catch(() => {});
    const t = trip();
    if (phase === "providers") {
      t.setProviders(null);
      t.setAgentPlaces([]);
      t.setStartPins([]);
      setCaption(null);
      setPhase(t.active ? "navigating" : "ready");
      return;
    }
    if (phase === "options") {
      // Back to the route card if one is active, otherwise clear the list.
      if (t.active) {
        setPhase("navigating");
        return;
      }
      t.setOptions([]);
      t.setParking({});
      t.setAgentPlaces([]);
      t.setStartPins([]);
      setCaption(null);
      setPhase("ready");
      return;
    }
    backToIdle();
  };

  const onEndRoute = () => {
    turn.cancel(); // a turn in flight is dropped with the route
    turn.endRoute();
    voice.cancelRecording().catch(() => {});
    voice.stopSpeaking();
    clearRoute();
  };


  /** Route state reset without touching audio (used by the copilot so its reply isn't cut). */
  const clearRoute = () => {
    const since = trip().drivingSince;
    if (recorder.isRecording()) recorder.stop().catch(() => {});
    const ended = trip().endRoute(); // the store clears the route, the options, the parking and the saved copy
    if (ended) {
      tripLog.log("route_ended", {
        place_id: ended.place.id, driving_s: since ? Math.round((Date.now() - since) / 1000) : 0,
        along_m: Math.round(guidance.alongM), remaining_m: Math.round(guidance.remainingM),
      }, ended.trip_id);
      tripLog.flush().catch(() => {});
    }
    clearSpeech(); // queued maneuver calls belong to the route that just ended
    setRoutesOpen(false);
    setCaption(null);
    setDialogPhase(null);
    setFit({ key: `end-${Date.now()}`, points: [loc.coords] });
  };

  // Hardware Back closes the top layer (src/utils/backAction.ts); only with nothing of ours open does
  // it leave the app. While driving it does nothing: one stray press must not drop the route.
  // The screen's own layers and handlers are mirrored at commit (like phaseRef above), the route and the
  // driving flag are read from the trip store, so the one listener registered on mount never acts on a
  // stale closure.
  const backRefs = useRef({
    state: { micCard: false, routesOpen: false, phase: "ready" as Phase },
    cancel: () => {},
    closeOptions: () => {},
    endRoute: () => {},
  });
  useLayoutEffect(() => {
    backRefs.current = {
      state: { micCard: !!micCard, routesOpen, phase },
      cancel: () => {
        turn.cancel();
        voice.cancelRecording().catch(() => {});
        turn.pause();
        backToIdle();
      },
      closeOptions: onClose,
      endRoute: onEndRoute,
    };
  });
  useEffect(() => {
    if (Platform.OS !== "android") return;
    const sub = BackHandler.addEventListener("hardwareBackPress", () => {
      const { state, cancel, closeOptions, endRoute } = backRefs.current;
      const t = trip();
      switch (backAction({ ...state, hasRoute: !!t.active, driving: t.driving })) {
        case "closeMicCard":
          setMicCard(null);
          return true;
        case "closeRoutes":
          setRoutesOpen(false);
          return true;
        case "cancelTurn":
          cancel();
          return true;
        case "closeOptions":
          closeOptions();
          return true;
        case "endRoute":
          endRoute();
          return true;
        case "ignore":
          return true;
        default:
          return false;
      }
    });
    return () => sub.remove();
  }, []);

  /** Re-plans the active route with the current truck profile (after a voice change) or to the entrance;
   * the stops on the way stay (they used to vanish from the line while their chips stayed on screen). */
  const reroute = async (cause: "profile" | "entrance" | "resume", failMsg = "Couldn't update the route. Try again."): Promise<boolean> => {
    const cur = trip().active;
    const gen = trip().routeGen;
    if (!cur) return false;
    try {
      // From where the truck is now, not where it was when the driver tapped the mic, and only through the
      // stops still ahead (a fresh /route knows no section index to drop the ones already made).
      const stops = stopsAhead(cur.route, cur.stops, coordsRef.current);
      const res = await api.route(coordsRef.current, cur.place, false, viaOf(stops), cur.trip_id ?? null);
      // The route must still be the one this started from (the store's generation): a stop added or an
      // off-route reroute that landed meanwhile is newer than this plan. The caller learns whether it landed.
      const applied = trip().applyRoute(gen, res.route, stops, res.parking);
      if (applied) warnRestricted(res.route);
      tripLog.log("reroute", { cause, ok: true, applied, restricted: res.route.restricted ?? null }, cur.trip_id);
      return applied;
    } catch (e) {
      tripLog.log("reroute", { cause, ok: false, no_route: isNoTruckRoute(e) }, cur.trip_id);
      if (trip().routeGen === gen) showToast(isNoTruckRoute(e) ? routeFailText(e) : failMsg);
      return false;
    }
  };

  /** "×" on a stop chip: re-plan through the remaining stops (plain /route when none are left). */
  const onRemoveStop = async (stop: Place) => {
    const cur = trip().active;
    const gen = trip().routeGen;
    if (!cur) return;
    const stops = stopsAhead(cur.route, cur.stops, loc.coords).filter((s) => s.id !== stop.id);
    const via = viaOf(stops);
    try {
      const res = via.length
        ? await api.reroute({
            lat: loc.coords.lat,
            lng: loc.coords.lng,
            place: cur.place,
            handle: null, // the old handle pins the old stops; a changed list must route fresh
            heading: loc.heading ?? null,
            section_index: 0,
            traveled_m: 0,
            via,
            trip_id: cur.trip_id ?? null,
          })
        : await api.route(coordsRef.current, cur.place, false, [], cur.trip_id ?? null);
      if (trip().applyRoute(gen, res.route, stops, res.parking)) {
        tripLog.log("stop_removed", { stops: stops.length, duration_s: res.route.duration_s }, cur.trip_id);
      }
    } catch {
      showToast("Couldn't update the stops. Try again.");
    }
  };

  /**
   * A tap in the routes list: swap `active.route` for that alternative, no network. The old primary
   * joins the alternatives so the driver can come back. A new route object resets the guidance
   * (spoken set, matcher, off-route tracker all key on it); the queue is emptied of the old one's lines.
   */
  const onPickAlternative = (i: number) => {
    const cur = trip().active;
    if (!cur) return;
    const route = trip().pickAlternative(i);
    if (!route) return;
    clearSpeech();
    tripLog.log("alternative_picked", { index: i, labels: route.labels ?? [], duration_s: route.duration_s, distance_m: route.distance_m }, cur.trip_id);
    setRoutesOpen(false);
    say(switchedLine(route, i), { priority: PRIORITY.MANEUVER, kind: "system", key: "route-switch", ttlMs: 15000 });
    // The other way may arrive from another street: the entry tip on the card follows it (cached verdict, no
    // new satellite call), and only while this route is still the one on screen.
    if (cur.place.category !== "service" && route.approach) {
      api
        .parking(cur.place, route.approach)
        .then((pk) => {
          if (trip().active?.route === route) trip().setActiveParking(cur.place.id, pk);
        })
        .catch(() => {});
    }
  };

  // Resume of a restored route: the first real fix after it decides whether the truck is still near the line.
  // A parked truck never trips the off-route tracker, so far from it the plan is redone right away.
  const replanOnFix = useRef(false);
  useEffect(() => {
    if (!replanOnFix.current || loc.isDemo) return;
    replanOnFix.current = false;
    const cur = trip().active;
    if (!cur || !trip().driving || !farFromRoute(cur.route.geometry, loc.coords)) return;
    say("Rerouting.", { priority: PRIORITY.MANEUVER, kind: "system", key: "reroute", ttlMs: 20000 });
    reroute("resume");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loc.ts, loc.isDemo]);

  // Back from the truck editor with new dimensions: same reroute as the voice change, nothing spoken.
  useEffect(() => {
    if (focused && takeTruckReroute() && trip().active) {
      reroute("profile", "Couldn't update the route for the new truck size.");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focused]);

  /**
   * One copilot reply: src/state/agentReducer.ts decides what happens (state patch + ordered effects),
   * src/state/applyEffects.ts runs the effects through the handlers below. `speak` is the promise that
   * `followParking` and `parkingFollowup` chain on; the dialog reopens the mic itself once it resolves.
   */
  const applyResponse = async (res: AgentResponse, ctx: AnswerContext): Promise<{ spoken: Promise<void> }> => {
    const t = trip();
    const { patch, effects } = reduceAgentResponse(res, {
      activePlaceId: t.active?.place.id ?? null,
      optionsCount: t.options.length,
      favoriteChains: getTruckProfile().favorite_chains,
    });
    if (patch.options) t.setOptions(patch.options);
    if (patch.parking) t.setParking(patch.parking);
    if (patch.places) t.setAgentPlaces(patch.places);
    if (patch.phase) setPhase(patch.phase);
    if (patch.activeRoute) {
      // Same destination, new route: the card, parking and dialog stay; only the line changes.
      const { route, stops, parking } = patch.activeRoute;
      const cur = t.active;
      if (cur && cur.place.id === res.place?.id) {
        const before = cur.stops?.length ?? cur.route.via?.length ?? 0;
        const after = stops.length;
        if (after !== before) tripLog.log(after > before ? "stop_added" : "stops_cleared", { stops: after, duration_s: route.duration_s }, cur.trip_id);
        else tripLog.log("route_replaced", { by: "copilot", duration_s: route.duration_s, labels: route.labels ?? [] }, cur.trip_id);
        t.applyRoute(t.routeGen, route, stops, parking);
      }
    }
    if (res.status === "not_understood" || res.status === "error") {
      tripLog.log("turn_failed", { status: res.status, text: res.transcript, intent: res.intent ?? null }, t.active?.trip_id);
    }
    return applyEffects(effects, res, {
      dial,
      research: (r) => runResearch(r, ctx),
      startRoute: (place, route, parking) => {
        startRoute({ place, route, parking });
        turn.routeSet();
      },
      speak: () => ctx.afterFiller.then(() => (ctx.isCurrent() ? sayReply(res, ctx.isCurrent) : undefined)),
      toast: showToast,
      backToIdle,
      pauseDialog: turn.pause,
      endDialog: turn.endRoute,
      continueDialog: () => {}, // the machine reopens the mic from the answer's status once the reply has played
      followParking: (place, spoken, approach) => followParking(place, spoken, ctx.isCurrent, approach),
      parkingFollowup: (placeId, spoken) => {
        const fPlace = [trip().active?.place, ...trip().options, ...trip().places()].find((p) => p?.id === placeId);
        if (!fPlace) return null;
        return parkingFollowup(fPlace, spoken, {
          isCurrent: ctx.isCurrent,
          busy: () => phaseRef.current === "listening" || phaseRef.current === "thinking",
          say: async (text, url) => {
            showLine(text);
            const a = toAudio(url);
            if (a) await voice.playAndWait(a, 6000);
          },
        });
      },
      clearRoute,
      startDriving: () => {
        turn.pause();
        setDriving(true);
      },
      openOptions: () => setPhase("options"),
      recenter: () => {
        setOverview(false);
        mapRef.current?.recenter();
      },
      overview: () => {
        const cur = trip().active;
        if (cur) {
          // Stay in driving mode; the camera stops following for a moment.
          setOverview(true);
          setFit({ key: `overview-${Date.now()}`, points: [loc.coords, cur.place] });
        }
      },
      zoomBy: (delta) => mapRef.current?.zoomBy(delta),
      setVoiceGuidance: setVoiceOn,
      setHandsFree,
      setNudges: setNudgesEnabled,
      saveProfile: (patch) => saveProfile({ ...getTruckProfile(), ...patch }),
      unknownAction: (action) => tripLog.log("turn_unknown_action", { action }, trip().active?.trip_id),
      // Save first so the new route uses the new profile.
      reroute: (patch) => saveProfile({ ...getTruckProfile(), ...patch }).then(() => reroute("profile")),
    });
  };

  const onPlacePress = async (p: Place) => {
    if (phase === "thinking") return;
    const t = trip();
    const prev = t.active;
    const gen = t.claimRoute(); // the last tap wins over a route still in flight
    turn.cancel(); // a copilot turn in flight gives way to the tap
    voice.stopSpeaking();
    setCaption(null);
    setPhase("thinking");
    setThinkingText(ROUTE_STAGE);
    try {
      // Breakdown providers: the driver needs the shop, not a satellite parking check (faster route).
      const isProvider = p.category === "service" || !!t.providers?.providers.some((x) => x.id === p.id);
      const res = await api.route(coordsRef.current, p, isProvider);
      if (trip().routeGen !== gen) return; // a newer route landed while this one was planned
      // Keep the previous destination among the alternatives.
      trip().setOptions((list) => [...(prev ? [prev.place] : []), ...list].filter((o) => o.id !== p.id));
      startRoute(res);
    } catch (e) {
      if (trip().routeGen !== gen) return;
      backToIdle();
      showToast(routeFailText(e));
    }
  };

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
        // Pins stay on the map while driving; a stray touch on one must not swap the destination mid-drive.
        // (The Route buttons in the options and providers lists still work: those are deliberate taps.)
        onPlacePress={(p) => {
          if (!trip().driving) onPlacePress(p);
        }}
        follow={following}
        onUserPan={() => {
          setTracking(false);
          if (driving) setOverview(true); // look around; following resumes after 15 s
        }}
        onMapTouch={Keyboard.dismiss}
      />

      {driving && active ? (
        <NavBanner
          topInset={insets.top}
          next={guidance.next}
          toNextM={guidance.toNextM}
          voiceOn={voiceOn}
          onToggleVoice={() => setVoiceOn(!voiceOn)}
          onLayout={setTopH}
          limitMph={advisories.limitMph}
          speedMph={advisories.speedMph}
          advisories={advisories.advisories}
          offlineLine={offline.backLine ?? (offline.offline ? "No signal · on the loaded route" : null)}
        />
      ) : (
      <TopBar
        topInset={insets.top}
        truckLabel={truckLabel(truck)}
        hazmat={truck.hazmat}
        destination={active?.place.name ?? null}
        onSearch={(q) =>
          // A bare place ("Reno") becomes "Take me to Reno"; a full request ("I need fuel") goes as typed.
          onSubmitText(/\b(?:i|i'm|me|my|need|find|take|go|where|show|get|park|near|any|truck ?stops?|hotels?|motels?|rest ?areas?|fuel|diesel|gas|parking)\b/i.test(q) ? q : `Take me to ${q}`)
        }
        blurGuard={searchBlurGuard}
        onTruckPress={() => router.push("/truck-profile")}
        onHistoryPress={() => router.push("/history")}
        tester={isTester()}
        simLabel={loc.sim?.label ?? null}
        onTesterPress={() => router.push("/tester")}
        isDemo={loc.resolved && loc.isDemo}
        onLocationPress={loc.enable}
        onLayout={setTopH}
      />
      )}

      <PressableScale
        testID="follow-button"
        accessibilityLabel={following ? "Stop following" : "Follow truck"}
        accessibilityState={{ selected: following }}
        onPress={() => {
          if (following) {
            setTracking(false);
            if (driving) setOverview(true);
          } else {
            setOverview(false);
            setTracking(true);
          }
        }}
        containerStyle={[styles.recenter, { bottom: chromeBottom }]}
        style={[styles.recenterBtn, shadow(colors), { backgroundColor: following ? colors.info : colors.surface }]}
      >
        <Icon name={following ? "navigation-variant" : "crosshairs-gps"} size={24} color={following ? colors.onInfo : colors.info} />
      </PressableScale>

      <CaptionBubble text={mapWindow >= MIN_MAP_FOR_BUBBLE ? (caption?.reply ?? null) : null} heard={caption?.heard} bottom={chromeBottom} />

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
        driving={driving}
        remaining={driving ? { m: guidance.remainingM, s: guidance.remainingS } : null}
        onGo={onGo}
        bottomInset={insets.bottom}
        onAvatarPress={onAvatarPress}
        onSubmitText={onSubmitText}
        lastReply={caption?.kind === "reply" ? caption.reply : null}
        onClose={onClose}
        onEndRoute={onEndRoute}
        onRemoveStop={onRemoveStop}
        onShowRoutes={() => setRoutesOpen(true)}
        offlineMap={offlineMap.state}
        onDownloadMap={offlineMap.download}
        onLayout={setSheetH}
        onPeekHeight={setPeekH}
        wakePhrase={null}
        panel={providers ? <ProvidersList data={providers} onRoute={(p) => onPlacePress(providerPlace(p))} /> : null}
      />

      {routesOpen && active && !driving && !!active.route.alternatives?.length && (
        <AlternativesSheet primary={active.route} bottom={chromeBottom} top={topH + spacing.sm} onPick={onPickAlternative} onClose={() => setRoutesOpen(false)} />
      )}

      {micCard && (
        <MicPermissionCard
          state={micCard}
          bottom={chromeBottom}
          onAllow={onAllowMic}
          onDismiss={() => setMicCard(null)}
        />
      )}

      {restored && active && (
        <RestoredBanner
          placeName={active.place.name}
          note={offlineMap.state?.kind === "saved" ? "Map saved" : null}
          top={topH + spacing.sm}
          onResume={() => {
            trip().dismissRestored();
            onGo();
            replanOnFix.current = true;
          }}
          onDismiss={clearRoute}
        />
      )}

      <Toast toast={toast} top={topH + spacing.sm} />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  recenter: { position: "absolute", right: spacing.lg },
  recenterBtn: {
    width: 56,
    height: 56,
    borderRadius: 28,
    alignItems: "center",
    justifyContent: "center",
  },
});
