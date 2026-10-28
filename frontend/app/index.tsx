import { useFocusEffect, useRouter } from "expo-router";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { AppState, Platform, StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { absoluteUrl, api } from "@/src/api";
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
import { logAnnouncement } from "@/src/dev/announcements";
import { isTester } from "@/src/auth";
import { routePointsAhead } from "@/src/geo/ahead";
import { swapAlternative, switchedLine } from "@/src/geo/routeChoice";
import { useDriverLocation } from "@/src/hooks/useDriverLocation";
import { useGuidance, type Guidance } from "@/src/hooks/useGuidance";
import { useOfflineGuidance, type OfflineGuidance } from "@/src/hooks/useOfflineGuidance";
import { useOfflineMap } from "@/src/hooks/useOfflineMap";
import { isOnline, useOnline } from "@/src/hooks/useOnline";
import { useRouteAdvisories } from "@/src/hooks/useRouteAdvisories";
import { useSpeechQueue } from "@/src/hooks/useSpeechQueue";
import { getTruckProfile, isOnboarded, saveTruckProfile, setHandsFree, takeTruckReroute, truckLabel, useHandsFree, useTruckProfile } from "@/src/hooks/useTruckProfile";
import { useVoice, type MicPermission } from "@/src/hooks/useVoice";
import { WAKE_SUPPORTED, useWakeWord } from "@/src/hooks/useWakeWord";
import { shadow, spacing, useTheme } from "@/src/theme";
import { setActiveRoute } from "@/src/state/activeRoute";
import { reduceAgentResponse } from "@/src/state/agentReducer";
import { offlineTurn } from "@/src/state/offlineTurn";
import { clearSavedRoute, loadActiveRoute, saveActiveRoute } from "@/src/state/routeStore";
import type { ActiveRoute, AgentResponse, LatLng, ParkingCheck, ParkingState, Phase, Place, Provider, ProvidersResult } from "@/src/types";
import { storage } from "@/src/utils/storage";
import { planFiller, type FillerPlan } from "@/src/voice/fillers";
import { PRIORITY } from "@/src/voice/queue";
import { buildSttHint, streetFromInstruction } from "@/src/voice/sttHint";
import { entrySpot, routeEndsAt } from "@/src/utils/entry";
import { parkingFollowup, toAudio } from "@/src/utils/parkingFollowup";

const VOICE_GUIDANCE_KEY = "voice_guidance_v1";
const RESEARCH_INTERIM = "Reading reviews now, almost there.";
const RESEARCH_INTERIM_MS = 7000;
const providerPlace = (p: Provider): Place => ({
  id: p.id, name: p.name, category: "service", lat: p.lat, lng: p.lng, tags: [],
  address: p.address, rating: p.rating, rating_count: p.rating_count, distance_mi: p.distance_mi,
});

const REQUEST_TIMEOUT_MS = 60000;
const NO_CONNECTION = "I can't reach the server right now. Check your signal and try again.";
const STT_SLOW = "Speech recognition is slow right now. Give it a moment and try again.";
const RATE_LIMITED = "One second, let me catch up.";
const STAGES = ["Searching nearby…", "Setting route…"];
const STAGE_MS = 3500;
const WAKE_SLEEP_MS = 10 * 60 * 1000; // background without a route: turn the wake word off
const SESSION_IDLE_MS = 10 * 60 * 1000; // no turn for this long: the next one starts a fresh session
const newSessionId = () => `dlg-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
// Nudges (/api/nudges): first ask a minute into the drive, then every five minutes; a nudge that
// is still waiting when the next one comes is stale. After one plays the mic opens for ~7 s.
const NUDGE_FIRST_MS = 60 * 1000;
const NUDGE_EVERY_MS = 5 * 60 * 1000;
const NUDGE_ANSWER_MS = 7000;
const NUDGE_KEY_PREFIX = "nudge-";
/** " There's a lot with trailers to the southeast, looks like a private yard." (or "") */
const nearbyLine = (pk: ParkingCheck) => {
  if (!pk.nearby) return "";
  const look = pk.nearby.kind === "unclear" ? "" : `, looks like a ${pk.nearby.kind}`;
  return ` There's a lot with trailers to the ${pk.nearby.direction}${look}.`;
};
const FILLER_DELAY_MS = 300; // after the driver stops talking; an answer faster than this needs no filler
const FILLER_SECOND_MS = 4000; // first clip played and still no answer: "Still looking."
const FILLER_SECOND = "Still looking.";
const SLOW_TURN_MS = 2500; // a previous turn slower than this: the filler is skipped less often
// Shortest first: planFiller() picks from the top more often.
const FILLERS = [
  "Hang on.",
  "On it.",
  "Checking.",
  "Okay, one sec.",
  "Got it, checking.",
  "Sure, one moment.",
  "Alright, let me look.",
  "Let me see what's around.",
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
  // Mirrors of the state the async handlers read after an await (they outlive the render that
  // made them). Written at commit, so a handler never sees a value from an uncommitted render.
  const phaseRef = useRef<Phase>("ready");
  useLayoutEffect(() => {
    phaseRef.current = phase;
  }, [phase]);
  const [active, setActive] = useState<ActiveRoute | null>(null);
  const [places, setPlaces] = useState<Place[]>([]);
  const [toast, setToast] = useState<ToastData | null>(null);
  const [caption, setCaption] = useState<{ reply: string; heard?: string } | null>(null);
  const [micCard, setMicCard] = useState<Exclude<MicPermission, "granted"> | null>(null);
  const [fit, setFit] = useState<{ key: string; points: LatLng[] }>({ key: "init", points: [] });
  const [thinkingText, setThinkingText] = useState<string | undefined>(undefined);
  const [options, setOptions] = useState<Place[]>([]);
  const [parking, setParking] = useState<Record<string, ParkingState>>({});
  const [driving, setDriving] = useState(false);
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
  const [voiceOn, setVoiceOnState] = useState(true);
  const { online } = useOnline();
  const [providers, setProviders] = useState<ProvidersResult | null>(null);
  const providersRef = useRef<ProvidersResult | null>(null);
  useLayoutEffect(() => {
    providersRef.current = providers;
  }, [providers]);

  useEffect(() => {
    storage.getItem(VOICE_GUIDANCE_KEY, true as boolean).then((v) => setVoiceOnState(v !== false));
  }, []);
  const setVoiceOn = (v: boolean) => {
    setVoiceOnState(v);
    storage.setItem(VOICE_GUIDANCE_KEY, v);
  };

  const turn = useRef(0);
  const watchdog = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stageTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const inflight = useRef<AbortController | null>(null);
  const filler = useRef<FillerPlan | null>(null); // the acknowledgement planned for the turn in flight
  const activeRef = useRef<ActiveRoute | null>(null);
  useLayoutEffect(() => {
    activeRef.current = active;
  }, [active]);
  const optionsRef = useRef<Place[]>([]);
  useLayoutEffect(() => {
    optionsRef.current = options;
  }, [options]);

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
    (item) => setCaption({ reply: item.text }),
    (item) => {
      if (item.key?.startsWith(NUDGE_KEY_PREFIX)) nudgeAnswer.current();
    },
  );

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

  const sayReply = (res: AgentResponse) => {
    setCaption({ reply: res.reply, heard: res.transcript || undefined });
    if (!res.audio_url) return Promise.resolve();
    // The playback timeouts are sized from the text behind each clip: the server says how it split
    // the reply when it can; older backends leave that to the sentence split.
    const parts = res.reply_parts?.length ? res.reply_parts : res.reply.split(/(?<=[.!?])\s+/);
    const [first, ...restParts] = parts;
    const rest = restParts.join(" ");
    const done = voice.playAndWait(absoluteUrl(res.audio_url), Math.min(20000, 1500 + first.length * 65));
    if (!res.audio_url_2) return done;
    const myTurn = turn.current;
    // Buffer the second half while the first plays, so it starts right after it (<150 ms gap).
    const url2 = absoluteUrl(res.audio_url_2);
    const next = voice.preload(url2);
    const available = fetch(url2, { headers: { Range: "bytes=0-0" } })
      .then((r) => r.ok)
      .catch(() => false);
    return done.then(async () => {
      if (myTurn !== turn.current) {
        next.remove();
        return;
      }
      if (!(await available)) {
        console.warn(`[voice] second half unavailable (${url2}); showing the full text instead`);
        next.remove();
        setCaption({ reply: res.reply, heard: res.transcript || undefined });
        return;
      }
      if (myTurn !== turn.current) {
        next.remove();
        return;
      }
      return voice.playAndWait(next, Math.min(20000, 1500 + (rest || res.reply).length * 65));
    });
  };

  // Instant acknowledgement while the real answer is on its way (cached TTS, rotated).
  // Keyed by text so the dev log can name the clip; the URL is what plays.
  const fillerUrls = useRef<Map<string, string>>(new Map());
  const lastFiller = useRef<string | null>(null);
  const lastTurnSlow = useRef(false); // previous turn's timings.total > SLOW_TURN_MS
  useEffect(() => {
    const texts = [...FILLERS, FILLER_SECOND];
    Promise.allSettled(texts.map((t) => api.speak(t).then((r) => absoluteUrl(r.audio_url)))).then((all) => {
      const m = new Map<string, string>();
      all.forEach((r, i) => {
        if (r.status === "fulfilled") m.set(texts[i], r.value);
      });
      fillerUrls.current = m;
    });
  }, []);
  /**
   * Acknowledgement planned the moment the driver stops talking: the first clip after
   * FILLER_DELAY_MS unless the answer is already there, "Still looking." once at FILLER_SECOND_MS
   * if it still is not. Short fillers (listed first) are picked more often; never the same twice
   * in a row; skipped 1 in 4 (1 in 10 after a slow turn).
   * Returns `cancel()` (true if nothing had played) and `done` (resolves when nothing more will play).
   */
  const scheduleFiller = () => {
    const urls = fillerUrls.current;
    const clips = FILLERS.filter((t) => urls.has(t));
    return planFiller({
      clips,
      secondClip: urls.has(FILLER_SECOND) ? FILLER_SECOND : null,
      delayMs: FILLER_DELAY_MS,
      secondMs: FILLER_SECOND_MS,
      skipRate: lastTurnSlow.current ? 0.1 : 0.25,
      lastClip: lastFiller.current,
      play: (clip) => {
        const url = urls.get(clip);
        if (!url) return Promise.resolve();
        if (clip !== FILLER_SECOND) lastFiller.current = clip;
        return voice.playAndWait(url, 4000);
      },
    });
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
  const followParking = async (place: Place, replyDone: Promise<void>, myTurn: number, approach = activeRef.current?.route.approach) => {
    if (place.category === "service") return; // breakdown provider: no truck-parking check
    try {
      const pk = await api.parking(place, approach);
      // The card always takes the result if it's still the same destination.
      setActive((a) => (a && a.place.id === place.id ? { ...a, parking: pk } : a));
      const e = entrySpot(pk);
      if (e && activeRef.current?.place.id === place.id && !routeEndsAt(activeRef.current.route, e)) reroute(myTurn); // route to E
      if (myTurn !== turn.current || activeRef.current?.place.id !== place.id) return;
      const entry = pk.entry_tip && pk.relative ? `The truck entrance is ${pk.entry_tip.charAt(0).toLowerCase()}${pk.entry_tip.slice(1)}` : pk.entry_tip;
      const tips = [entry, pk.park_tip].filter(Boolean).join(". ");
      const text =
        pk.verdict === "unlikely"
          ? `Heads up: from the satellite, parking there looks tight for a rig.${nearbyLine(pk)}`
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
  // dialog.current = the backend session (found places, "the second one", rejected, reply rotation).
  // live.current = the hands-free loop (mic reopens after each answer). A route stops the loop but keeps
  // the session; a fresh session only after end_route, bye, 2 misses or 10 min without a turn.
  const dialog = useRef<string | null>(null);
  const live = useRef(false);
  const lastTurnAt = useRef(0);
  const misses = useRef(0);
  const endDialog = () => {
    dialog.current = null;
    live.current = false;
    misses.current = 0;
  };
  const pauseDialog = () => {
    live.current = false;
  };
  const sessionStale = () => !dialog.current || Date.now() - lastTurnAt.current > SESSION_IDLE_MS;
  const ensureSession = () => {
    if (sessionStale()) {
      dialog.current = newSessionId();
      misses.current = 0;
    }
    lastTurnAt.current = Date.now();
  };
  const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

  /**
   * After an answer: keep talking unless the conversation is over. `force` (a confirm question,
   * "Did you say Reno?") reopens the mic even when the loop was paused: the next "yes" / "no"
   * resolves it. beginListening turns the loop back on from there.
   */
  const continueDialog = async (myTurn: number, res: AgentResponse | null, spoken: Promise<void>, force = false) => {
    if (!dialog.current) return;
    misses.current = res && res.status !== "not_understood" ? 0 : misses.current + 1;
    if (res?.end_conversation || misses.current >= 2) {
      endDialog();
      return;
    }
    if (!live.current && !force) return;
    await spoken;
    if (myTurn !== turn.current || (!live.current && !force)) return;
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

  /**
   * Drops the turn in flight: the late answer is discarded (turn counter), the request is aborted,
   * the acknowledgement clip never starts (or is cut), the thinking stages stop. The dialog session
   * stays, so the next turn lands on the same backend conversation. The phase is the caller's to set.
   */
  const cancelTurn = () => {
    clearTimers();
    turn.current++;
    inflight.current?.abort();
    inflight.current = null;
    filler.current?.cancel();
    filler.current = null;
    voice.stopSpeaking();
    setThinkingText(undefined);
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
    setPhase(activeRef.current ? "navigating" : providersRef.current ? "providers" : optionsRef.current.length ? "options" : "ready");

  // Dev tools (tester replay) read the active route from here; the disk copy survives a restart.
  // Every change of `active` (new route, alternative, stops, reroute, parking) is written; the
  // object that was just restored is not written again, so its saved time stays the original.
  const skipSave = useRef<ActiveRoute | null>(null);
  useEffect(() => {
    setActiveRoute(active);
    if (active && active !== skipSave.current) saveActiveRoute(active);
  }, [active]);

  // A route saved before the last restart (crash, dead zone, phone off) comes back silently, not
  // driving, with a banner: Resume drives it, Dismiss forgets it.
  const [restoredId, setRestoredId] = useState<string | null>(null);
  useEffect(() => {
    loadActiveRoute().then((saved) => {
      if (!saved || activeRef.current) return;
      skipSave.current = saved.active;
      routeStart.current = Date.now();
      setActive(saved.active);
      setPhase("navigating");
      setRestoredId(saved.active.place.id);
      setFit({ key: `restored-${saved.active.place.id}`, points: [loc.coords, saved.active.place] });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const restored = restoredId !== null && active?.place.id === restoredId && !driving;

  /**
   * The tracker says the truck left the route: ask the engine to resume the plan from here
   * (/reroute with the handle, section and metres into it), or plan afresh when that fails.
   * One at a time; the new route only lands if the destination is still the same.
   * Not for a tester parked on a fake spot (no trace = nothing moves) and not before "Go".
   */
  const rerouting = useRef(false);
  const offlineRef = useRef<OfflineGuidance | null>(null);
  const rerouteOffRoute = async (g: Guidance) => {
    const cur = activeRef.current;
    if (!cur || rerouting.current || !driving) return;
    if (loc.sim && !loc.sim.trace) return;
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
        });
      } catch (e) {
        console.log(`[reroute] /reroute failed (${String(e)}), planning from scratch`);
        res = await api.route(loc.coords, cur.place);
      }
      if (activeRef.current?.place.id === cur.place.id) {
        setActive((a) => (a ? { ...a, route: res.route } : a));
      }
      g.resetOffRoute();
    } catch {
      showToast("Couldn't reroute. Follow the map for now.");
    } finally {
      rerouting.current = false;
    }
  };
  // One object per GPS fix (not per render): useGuidance pushes each fix into the off-route tracker once.
  const fix = useMemo(
    () => ({ ...loc.coords, speed: loc.speed, accuracy: loc.accuracy, ts: loc.ts }),
    [loc.coords, loc.speed, loc.accuracy, loc.ts],
  );
  const guidance = useGuidance(active, fix, driving, voiceOn, say, rerouteOffRoute);
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

  // ---- Nudges: while driving, every few minutes the backend may have something to offer (a
  // break coming due, parking ahead). Each line is spoken as ambient; once it has played the mic
  // opens for a few seconds (listenForAnswer) and the backend keeps the offer pending for the next
  // turn of the same session, so "yes" is enough. Not offline, not before "Go". ----
  const drivingSince = useRef<number | null>(null);
  useEffect(() => {
    drivingSince.current = driving ? Date.now() : null;
  }, [driving]);
  const hasRoute = !!active;
  useEffect(() => {
    if (!driving || !hasRoute) return;
    const ctrl = new AbortController();
    let every: ReturnType<typeof setInterval> | null = null;
    const poll = async () => {
      if (!isOnline()) return;
      const cur = activeRef.current;
      if (!cur) return;
      const g = guidanceRef.current;
      // The offer must land in the session the answer will use: a live one, else one started here.
      const stale = sessionStale();
      const before = dialog.current;
      const sessionId = stale || !before ? newSessionId() : before;
      const since = drivingSince.current;
      try {
        const { nudges } = await api.nudges(
          {
            lat: coordsRef.current.lat,
            lng: coordsRef.current.lng,
            driving: true,
            active_place: cur.place,
            ahead: routePointsAhead(cur.route.geometry, g.cum, g.matchedIndex, g.alongM),
            drive_elapsed_min: since === null ? null : Math.min(24 * 60, Math.max(0, Math.round((Date.now() - since) / 60000))),
            local_hour: new Date().getHours(),
            session_id: sessionId,
          },
          ctrl.signal,
        );
        if (ctrl.signal.aborted || !nudges.length) return;
        if (stale && dialog.current === before) {
          dialog.current = sessionId;
          misses.current = 0;
        }
        if (dialog.current === sessionId) lastTurnAt.current = Date.now(); // the answer must not start a fresh session
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
  }, [driving, hasRoute, say]); // refs and a stable callback; `active` itself must not restart the timer

  const onGo = () => {
    pauseDialog();
    voice.stopSpeaking();
    setCaption(null);
    setRoutesOpen(false);
    setDriving(true);
  };

  /** Typed request: same pipeline and session as voice, without recording or reopening the mic.
   *  Sent while the copilot is still on the last request, it replaces that request (barge-in). */
  const onSubmitText = (text: string): boolean => {
    if (phaseRef.current === "thinking") cancelTurn();
    voice.stopSpeaking();
    voice.cancelRecording().catch(() => {});
    pauseDialog();
    ensureSession();
    setCaption(null);
    submitTurn(undefined, text);
    return true;
  };

  const submitTurn = async (recorded?: string, typed?: string) => {
    clearTimers();
    const myTurn = ++turn.current;
    // No signal: nothing to transcribe with and nobody to ask. Said, shown, and the turn is over
    // (no STT, no /agent). The route, the turns and the advisories keep working from what we loaded.
    if (offlineTurn({ isOnline, say, setCaption })) {
      voice.cancelRecording().catch(() => {});
      pauseDialog();
      backToIdle();
      return;
    }
    setPhase("thinking");
    startStages();
    const ctrl = new AbortController();
    inflight.current = ctrl;
    const timer = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
    let plan: FillerPlan | null = null;
    let sttMs: number | null = null;
    let sttConfidence: number | null = null;
    try {
      let transcript = typed ?? "";
      if (typed === undefined) {
        const uri = recorded ?? (await voice.stopRecording());
        if (!uri) throw new Error("no recording");
        // Planned before STT, so the driver hears something ~300 ms after they stop talking
        // (the transcript alone takes 1-1.5 s to come back).
        plan = scheduleFiller();
        filler.current = plan;
        // Names the recogniser should expect: what is on screen and on the route, most useful first.
        const cur = activeRef.current;
        const hint = buildSttHint([
          cur?.place.name,
          ...optionsRef.current.map((p) => p.name),
          ...places.map((p) => p.name),
          ...(cur?.stops ?? []).map((s) => s.name),
          ...(cur?.route.labels ?? []),
          streetFromInstruction(guidanceRef.current.next?.instruction),
        ]);
        const stt = await api.transcribe(uri, ctrl.signal, hint || undefined);
        sttMs = stt.stt_ms;
        sttConfidence = stt.stt_confidence;
        transcript = stt.transcript;
        if (!transcript) plan.cancel(); // nothing heard: no "Hang on." for silence
      }
      if (myTurn !== turn.current) return;
      if (transcript) setCaption({ reply: "…", heard: transcript });
      const res = await api.agentSpeak(
        transcript,
        loc.coords,
        activeRef.current,
        ctrl.signal,
        dialog.current ?? undefined,
        {
          driving,
          voice_guidance: voiceOn,
          hands_free: handsFree && WAKE_SUPPORTED,
          options_count: optionsRef.current.length,
        },
        loc.heading ?? null,
        limitRef.current,
        sttMs,
        sttConfidence,
        loc.speed,
      );
      if (myTurn !== turn.current) return;
      lastTurnSlow.current = (res.timings?.total ?? 0) > SLOW_TURN_MS;
      // Fast answer: the filler never starts. Otherwise the answer waits for it to finish.
      plan?.cancel();
      const afterFiller = plan?.done ?? Promise.resolve();
      if (__DEV__ && plan) {
        // Ms after the driver stopped talking. No filler on air: the reply is the first sound, about now.
        const at = plan.startedAt();
        const line = `[turn] filler=${at === null ? "none" : plan.picked} at=${at ?? "-"} first_audio=${at ?? plan.elapsed()}`;
        console.log(line);
        logAnnouncement(line, "system");
      }
      const speak = () => afterFiller.then(() => (myTurn === turn.current ? sayReply(res) : undefined));
      await applyAgentResponse(res, myTurn, ctrl.signal, afterFiller, speak);
    } catch (e) {
      plan?.cancel();
      if (myTurn !== turn.current) return; // cancelled by the driver (barge-in, close): nothing to report
      const err = String(e);
      const msg = err.includes("422")
        ? "Check your truck profile and try again."
        : err.includes("502")
          ? MESSAGES.placesUnavailable
          : err.includes("504")
            ? STT_SLOW
            : err.includes("429")
              ? RATE_LIMITED
              : /abort|network|timed? ?out|failed to fetch/i.test(err)
                ? NO_CONNECTION
                : MESSAGES.notUnderstood;
      setCaption({ reply: msg });
      backToIdle();
      continueDialog(myTurn, null, wait(1200));
    } finally {
      clearTimeout(timer);
      if (myTurn !== turn.current) plan?.cancel(); // turn abandoned: never start its filler
      if (myTurn === turn.current) clearTimers();
      if (filler.current === plan) filler.current = null;
    }
  };

  /** Breakdown: say "give me a minute", wait for the research, an interim line at ~7 s, then the list. */
  const runResearch = async (res: AgentResponse, myTurn: number, signal: AbortSignal) => {
    clearTimers();
    setThinkingText("Checking who's around…");
    const intro = sayReply(res);
    let done = false;
    const interimAudio = api.speak(RESEARCH_INTERIM).catch(() => null);
    let interimPlayed: Promise<unknown> = Promise.resolve();
    const interim = setTimeout(() => {
      if (done || myTurn !== turn.current) return;
      setThinkingText("Reading reviews…");
      interimPlayed = intro.then(async () => {
        const a = await interimAudio;
        if (a && !done && myTurn === turn.current) await voice.playAndWait(absoluteUrl(a.audio_url), 4000);
      });
    }, RESEARCH_INTERIM_MS);
    let out: AgentResponse;
    try {
      out = await api.breakdownResult(res.research_id!, signal);
    } finally {
      done = true; // a failed research must not play "Reading reviews" afterwards
      clearTimeout(interim);
    }
    if (myTurn !== turn.current) return;
    await intro;
    await interimPlayed;
    if (myTurn !== turn.current) return;
    if (out.status === "providers" && out.providers?.length) {
      const list = out.providers;
      setProviders({ providers: list, call_card: out.call_card, summary: out.summary, provider_source: out.provider_source });
      setPlaces(list.map(providerPlace));
      setPhase("providers");
      setFit({ key: `providers-${myTurn}`, points: [loc.coords, ...list] }); // one research per turn: unique
    } else backToIdle();
    continueDialog(myTurn, out, sayReply(out));
  };

  // Hands-free: listening stops by itself when the driver pauses (tap to stop early).
  // `maxMs` caps the window (a nudge's "yes" window); the default is a full turn.
  const beginListening = async (fromWake = false, maxMs?: number) => {
    // Offline the mic stays shut: recording seven seconds for a transcription that cannot happen helps nobody.
    if (offlineTurn({ isOnline, say, setCaption })) {
      pauseDialog();
      return;
    }
    ensureSession();
    live.current = true;
    setPhase("listening");
    clearTimers();
    const myTurn = ++turn.current;
    let res: string;
    try {
      res = await voice.listen({ chime: !fromWake, maxMs }); // the wake word plays its own chime
    } catch {
      pauseDialog();
      showToast(MESSAGES.notUnderstood);
      backToIdle();
      return;
    }
    if (res === "cancelled" || myTurn !== turn.current) return;
    if (res === "silent") {
      // Silence stops listening; on the very first turn tell the driver we heard nothing.
      if (misses.current === 0 && !caption) setCaption({ reply: MESSAGES.notUnderstood });
      pauseDialog();
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
    beginListening(true);
  });
  /** The recorder and the wake word can't share the mic (Android): pause it first. */
  const listenNow = async () => {
    await wake.stop();
    beginListening();
  };
  /** A nudge just played: a short mic window for the "yes", unless a turn is already under way. */
  const listenForAnswer = async () => {
    if (phaseRef.current === "listening" || phaseRef.current === "thinking") return;
    await wake.stop();
    beginListening(false, NUDGE_ANSWER_MS);
  };
  useEffect(() => {
    nudgeAnswer.current = listenForAnswer;
  });

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

  const idle = phase === "ready" || phase === "navigating" || phase === "options" || phase === "providers";
  // iOS v1 has no background audio mode: the wake word listens only while the app is on screen.
  const wakeArmed = wake.supported && handsFree && micOk && !asleep && (Platform.OS !== "ios" || appActive);
  // Not while a reply plays: starting the wake engine switches the audio session under the player
  // (iOS moves the voice to the earpiece mid-sentence, Porcupine can error out and stay dead).
  useEffect(() => {
    if (!wakeArmed) wake.shutdown();
    else if (focused && idle && !voice.speaking) wake.start();
    else wake.stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wakeArmed, focused, idle, voice.speaking]);
  // Keep the recorder ready for an instant tap-to-talk, unless the wake word holds the mic.
  const { setPrewarm } = voice;
  useEffect(() => {
    setPrewarm(micOk && focused && !wakeArmed);
  }, [setPrewarm, micOk, focused, wakeArmed]);


  const onAvatarPress = async () => {
    if (phase === "listening") {
      voice.finishListening();
      return;
    }
    // Barge-in: a tap while the copilot is still working drops that turn and listens again
    // (same session, so "no, the other one" lands where it should).
    if (phase === "thinking") {
      cancelTurn();
      backToIdle();
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
    pauseDialog();
    cancelTurn(); // discard any in-flight turn
    voice.cancelRecording().catch(() => {});
    if (phase === "providers") {
      setProviders(null);
      setPlaces([]);
      setCaption(null);
      setPhase(activeRef.current ? "navigating" : "ready");
      return;
    }
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
    clearRoute();
  };

  /** Route state reset without touching audio (used by the copilot so its reply isn't cut). */
  const clearRoute = () => {
    routeStart.current = null;
    clearSpeech(); // queued maneuver calls belong to the route that just ended
    clearSavedRoute();
    setRestoredId(null);
    setDriving(false);
    setRoutesOpen(false);
    setActive(null);
    setCaption(null);
    setOptions([]);
    setParking({});
    // A route to a breakdown provider: back to the providers list.
    const prov = providersRef.current;
    setPlaces(prov ? prov.providers.map(providerPlace) : []);
    setPhase(prov ? "providers" : "ready");
    setFit({ key: `end-${Date.now()}`, points: [loc.coords] });
  };

  /** Re-plans the active route with the current truck profile (after a voice change). */
  const reroute = async (myTurn: number, failMsg = "Couldn't update the route. Try again.") => {
    const cur = activeRef.current;
    if (!cur) return;
    try {
      const res = await api.route(loc.coords, cur.place);
      // Only the destination must still match: a newer turn shouldn't keep the old route on screen.
      if (activeRef.current?.place.id === cur.place.id) {
        setActive((a) => (a ? { ...a, route: res.route } : a));
      }
    } catch {
      if (myTurn === turn.current) showToast(failMsg);
    }
  };

  /** "×" on a stop chip: re-plan through the remaining stops (plain /route when none are left). */
  const onRemoveStop = async (stop: Place) => {
    const cur = activeRef.current;
    if (!cur) return;
    const stops = (cur.stops ?? []).filter((s) => s.id !== stop.id);
    const via = stops.map((s): [number, number] => [s.lat, s.lng]);
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
          })
        : await api.route(loc.coords, cur.place);
      if (activeRef.current?.place.id === cur.place.id) {
        setActive((a) => (a ? { ...a, route: res.route, stops } : a));
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
    const cur = activeRef.current;
    if (!cur) return;
    const { route } = swapAlternative(cur.route, i);
    if (route === cur.route) return;
    clearSpeech();
    setActive({ ...cur, route });
    setRoutesOpen(false);
    say(switchedLine(route, i), { priority: PRIORITY.MANEUVER, kind: "system", key: "route-switch", ttlMs: 15000 });
  };

  // Back from the truck editor with new dimensions: same reroute as the voice change, nothing spoken.
  useEffect(() => {
    if (focused && takeTruckReroute() && activeRef.current) {
      reroute(turn.current, "Couldn't update the route for the new truck size.");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focused]);

  /**
   * One copilot reply: src/state/agentReducer.ts decides what happens (state patch + ordered effects),
   * this applies it. Effects run in order; `speak` sets the promise that `followParking`,
   * `parkingFollowup` and `continueDialog` chain on. Only `research` awaits (it waits for the
   * breakdown result); everything else runs synchronously in this tick, as the inline code used to.
   */
  const applyAgentResponse = async (res: AgentResponse, myTurn: number, signal: AbortSignal, afterFiller: Promise<void>, speak: () => Promise<void>) => {
    const { patch, effects } = reduceAgentResponse(res, {
      activePlaceId: activeRef.current?.place.id ?? null,
      optionsCount: optionsRef.current.length,
      favoriteChains: getTruckProfile().favorite_chains,
    });
    if (patch.options) setOptions(patch.options);
    if (patch.parking) setParking(patch.parking);
    if (patch.places) setPlaces(patch.places);
    if (patch.phase) setPhase(patch.phase);
    if (patch.activeRoute) {
      // Same destination, new route: the card, parking and dialog stay; only the line changes.
      const { route, stops } = patch.activeRoute;
      setActive((a) => (a && a.place.id === res.place?.id ? { ...a, route, stops } : a));
    }
    let spoken: Promise<void> = Promise.resolve();
    for (const e of effects) {
      switch (e.type) {
        case "dial":
          dial(e.phone);
          break;
        case "research":
          await afterFiller;
          await runResearch(res, myTurn, signal);
          break;
        case "startRoute":
          startRoute({ place: e.place, route: e.route, parking: e.parking });
          break;
        case "speak":
          spoken = speak();
          break;
        case "toast":
          showToast(e.text);
          break;
        case "backToIdle":
          backToIdle();
          break;
        case "pauseDialog":
          pauseDialog();
          break;
        case "endDialog":
          endDialog();
          break;
        case "continueDialog":
          continueDialog(myTurn, res, spoken, e.force);
          break;
        case "followParking":
          followParking(e.place, spoken, myTurn, e.approach);
          break;
        case "parkingFollowup": {
          const fPlace = [activeRef.current?.place, ...optionsRef.current, ...places].find((p) => p?.id === e.placeId);
          if (!fPlace) break;
          spoken = parkingFollowup(fPlace, spoken, {
            isCurrent: () => myTurn === turn.current,
            busy: () => phaseRef.current === "listening" || phaseRef.current === "thinking",
            say: async (text, url) => {
              setCaption({ reply: text });
              const a = toAudio(url);
              if (a) await voice.playAndWait(a, 6000);
            },
          });
          break;
        }
        case "clearRoute":
          clearRoute();
          break;
        case "startDriving":
          pauseDialog();
          setDriving(true);
          break;
        case "openOptions":
          setPhase("options");
          break;
        case "recenter":
          setOverview(false);
          mapRef.current?.recenter();
          break;
        case "overview":
          if (activeRef.current) {
            // Stay in driving mode; the camera stops following for a moment.
            setOverview(true);
            setFit({ key: `overview-${Date.now()}`, points: [loc.coords, activeRef.current.place] });
          }
          break;
        case "zoomBy":
          mapRef.current?.zoomBy(e.delta);
          break;
        case "setVoiceGuidance":
          setVoiceOn(e.on);
          break;
        case "setHandsFree":
          setHandsFree(e.on);
          break;
        case "saveProfile":
          saveTruckProfile({ ...getTruckProfile(), ...e.patch });
          break;
        case "reroute":
          // Save first so the new route uses the new profile.
          saveTruckProfile({ ...getTruckProfile(), ...e.patch }).then(() => reroute(myTurn));
          break;
      }
    }
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
      // Breakdown providers: the driver needs the shop, not a satellite parking check (faster route).
      const isProvider = p.category === "service" || !!providersRef.current?.providers.some((x) => x.id === p.id);
      const res = await api.route(loc.coords, p, isProvider);
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
        follow={following}
        onUserPan={() => {
          setTracking(false);
          if (driving) setOverview(true); // look around; following resumes after 15 s
        }}
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
        containerStyle={[styles.recenter, { bottom: sheetH + spacing.lg }]}
        style={[styles.recenterBtn, shadow(colors), { backgroundColor: following ? colors.info : colors.surface }]}
      >
        <Icon name={following ? "navigation-variant" : "crosshairs-gps"} size={24} color={following ? colors.onInfo : colors.info} />
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
        driving={driving}
        remaining={driving ? { m: guidance.remainingM, s: guidance.remainingS } : null}
        onGo={onGo}
        bottomInset={insets.bottom}
        onAvatarPress={onAvatarPress}
        onSubmitText={onSubmitText}
        onClose={onClose}
        onEndRoute={onEndRoute}
        onRemoveStop={onRemoveStop}
        onShowRoutes={() => setRoutesOpen(true)}
        offlineMap={offlineMap.state}
        onDownloadMap={offlineMap.download}
        onLayout={setSheetH}
        wakePhrase={wake.active ? wake.phrase : null}
        panel={providers ? <ProvidersList data={providers} onRoute={(p) => onPlacePress(providerPlace(p))} /> : null}
      />

      {routesOpen && active && !driving && !!active.route.alternatives?.length && (
        <AlternativesSheet primary={active.route} bottom={sheetH + spacing.lg} onPick={onPickAlternative} onClose={() => setRoutesOpen(false)} />
      )}

      {micCard && (
        <MicPermissionCard
          state={micCard}
          bottom={sheetH + spacing.lg}
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
            setRestoredId(null);
            onGo();
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
