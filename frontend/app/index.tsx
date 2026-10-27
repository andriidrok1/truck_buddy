import { useFocusEffect, useRouter } from "expo-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppState, Platform, StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { absoluteUrl, api } from "@/src/api";
import { CopilotSheet } from "@/src/components/CopilotSheet";
import { Icon } from "@/src/components/Icon";
import MapLayer from "@/src/components/MapLayer";
import type { MapLayerHandle } from "@/src/components/mapTypes";
import { MicPermissionCard } from "@/src/components/MicPermissionCard";
import { NavBanner } from "@/src/components/NavBanner";
import { PressableScale } from "@/src/components/PressableScale";
import { dial, ProvidersList } from "@/src/components/ProvidersList";
import { CaptionBubble, Toast, type ToastData } from "@/src/components/Toast";
import { TopBar } from "@/src/components/TopBar";
import { MESSAGES } from "@/src/constants";
import { isTester } from "@/src/auth";
import { useDriverLocation } from "@/src/hooks/useDriverLocation";
import { useGuidance, type Guidance } from "@/src/hooks/useGuidance";
import { useSpeechQueue } from "@/src/hooks/useSpeechQueue";
import { getTruckProfile, isOnboarded, saveTruckProfile, setHandsFree, takeTruckReroute, truckLabel, useHandsFree, useTruckProfile } from "@/src/hooks/useTruckProfile";
import { useVoice, type MicPermission } from "@/src/hooks/useVoice";
import { WAKE_SUPPORTED, useWakeWord } from "@/src/hooks/useWakeWord";
import { shadow, spacing, useTheme } from "@/src/theme";
import { setActiveRoute } from "@/src/state/activeRoute";
import { reduceAgentResponse } from "@/src/state/agentReducer";
import type { ActiveRoute, AgentResponse, LatLng, ParkingCheck, ParkingState, Phase, Place, Provider, ProvidersResult } from "@/src/types";
import { storage } from "@/src/utils/storage";
import { PRIORITY } from "@/src/voice/queue";
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
/** " There's a lot with trailers to the southeast, looks like a private yard." (or "") */
const nearbyLine = (pk: ParkingCheck) => {
  if (!pk.nearby) return "";
  const look = pk.nearby.kind === "unclear" ? "" : `, looks like a ${pk.nearby.kind}`;
  return ` There's a lot with trailers to the ${pk.nearby.direction}${look}.`;
};
const FILLER_DELAY_MS = 300; // answer faster than this: no filler at all
// Shortest first: scheduleFiller() picks from the top more often.
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
  const phaseRef = useRef<Phase>("ready");
  phaseRef.current = phase;
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
  // "Follow" button: the camera keeps the truck arrow in view until the driver drags the map.
  const [tracking, setTracking] = useState(false);
  const following = tracking || (driving && !overview);
  // A new view to show (route, options, overview) wins over the Follow button.
  useEffect(() => {
    if (fit.key !== "init" && fit.key !== "gps") setTracking(false);
  }, [fit.key]);
  // Whole-route view while driving: back to following the truck after 15 s.
  useEffect(() => {
    if (!overview) return;
    const t = setTimeout(() => setOverview(false), 15000);
    return () => clearTimeout(t);
  }, [overview]);
  const [voiceOn, setVoiceOnState] = useState(true);
  const [providers, setProviders] = useState<ProvidersResult | null>(null);
  const providersRef = useRef<ProvidersResult | null>(null);
  providersRef.current = providers;

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
  const activeRef = useRef<ActiveRoute | null>(null);
  activeRef.current = active;
  const optionsRef = useRef<Place[]>([]);
  optionsRef.current = options;

  const showToast = useCallback((text: string, kind: ToastData["kind"] = "error") => {
    setToast({ id: Date.now(), text, kind });
  }, []);

  // Everything the copilot says on its own (maneuvers, reroutes, reminders) goes through one
  // queue: it waits while the driver is talking to the copilot or a reply plays (never cuts it
  // off), and orders by urgency. The caption follows whatever line is on air.
  const { say, clear: clearSpeech } = useSpeechQueue(
    voice,
    () => phaseRef.current === "listening" || phaseRef.current === "thinking",
    (item) => setCaption({ reply: item.text }),
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

  // Re-center once when the real GPS fix arrives.
  useEffect(() => {
    if (!loc.isDemo) {
      center.current = loc.coords;
      setFit({ key: "gps", points: [loc.coords] });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loc.isDemo]);

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
  const fillers = useRef<string[]>([]);
  const lastFiller = useRef(-1);
  useEffect(() => {
    Promise.allSettled(FILLERS.map((t) => api.speak(t).then((r) => absoluteUrl(r.audio_url)))).then((all) => {
      fillers.current = all.flatMap((r) => (r.status === "fulfilled" ? [r.value] : []));
    });
  }, []);
  /**
   * Acknowledgement that starts after FILLER_DELAY_MS unless the answer is already there.
   * Short fillers (listed first) are picked more often; never the same twice in a row; skipped 1 in 4.
   * Returns `cancel()` (true if the filler hadn't started) and `done` (resolves when it finished).
   */
  const scheduleFiller = () => {
    const n = fillers.current.length;
    if (!n || Math.random() < 0.25) return null;
    let i = Math.floor(n * Math.random() ** 2); // biased towards the short ones at the top
    if (n > 1 && i === lastFiller.current) i = (i + 1) % n;
    let started = false;
    let resolveDone: () => void = () => {};
    const done = new Promise<void>((r) => (resolveDone = r));
    const timer = setTimeout(() => {
      started = true;
      lastFiller.current = i;
      voice.playAndWait(fillers.current[i], 4000).then(resolveDone);
    }, FILLER_DELAY_MS);
    return {
      done,
      cancel: () => {
        clearTimeout(timer);
        if (!started) resolveDone();
        return !started;
      },
    };
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
  const ensureSession = () => {
    if (!dialog.current || Date.now() - lastTurnAt.current > SESSION_IDLE_MS) {
      dialog.current = `dlg-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      misses.current = 0;
    }
    lastTurnAt.current = Date.now();
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
    if (!live.current) return;
    await spoken;
    if (myTurn !== turn.current || !live.current) return;
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
    setPhase(activeRef.current ? "navigating" : providersRef.current ? "providers" : optionsRef.current.length ? "options" : "ready");

  // Dev tools (tester replay) read the active route from here.
  useEffect(() => {
    setActiveRoute(active);
  }, [active]);

  /**
   * The tracker says the truck left the route: ask the engine to resume the plan from here
   * (/reroute with the handle, section and metres into it), or plan afresh when that fails.
   * One at a time; the new route only lands if the destination is still the same.
   * Not for a tester parked on a fake spot (no trace = nothing moves) and not before "Go".
   */
  const rerouting = useRef(false);
  const rerouteOffRoute = async (g: Guidance) => {
    const cur = activeRef.current;
    if (!cur || rerouting.current || !driving) return;
    if (loc.sim && !loc.sim.trace) return;
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

  const onGo = () => {
    pauseDialog();
    voice.stopSpeaking();
    setCaption(null);
    setDriving(true);
  };

  /** Typed request: same pipeline and session as voice, without recording or reopening the mic.
   *  Returns false (text kept by the caller) while the copilot is still on the last request. */
  const onSubmitText = (text: string): boolean => {
    if (phaseRef.current === "thinking") {
      showToast("One second, finishing the last request", "info");
      return false;
    }
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
    setPhase("thinking");
    startStages();
    const ctrl = new AbortController();
    inflight.current = ctrl;
    const timer = setTimeout(() => ctrl.abort(), REQUEST_TIMEOUT_MS);
    let filler: ReturnType<typeof scheduleFiller> = null;
    try {
      let transcript = typed ?? "";
      if (typed === undefined) {
        const uri = recorded ?? (await voice.stopRecording());
        if (!uri) throw new Error("no recording");
        transcript = (await api.transcribe(uri, ctrl.signal)).transcript;
      }
      if (myTurn !== turn.current) return;
      if (transcript) {
        if (typed === undefined) filler = scheduleFiller(); // only when the driver actually said something
        setCaption({ reply: "…", heard: transcript });
      }
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
      );
      if (myTurn !== turn.current) return;
      // Fast answer: the filler never starts. Otherwise the answer waits for it to finish.
      filler?.cancel();
      const afterFiller = filler?.done ?? Promise.resolve();
      const speak = () => afterFiller.then(() => (myTurn === turn.current ? sayReply(res) : undefined));
      await applyAgentResponse(res, myTurn, ctrl.signal, afterFiller, speak);
    } catch (e) {
      filler?.cancel();
      if (myTurn !== turn.current) return;
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
      if (myTurn !== turn.current) filler?.cancel(); // turn abandoned: never start its filler
      if (myTurn === turn.current) clearTimers();
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
      setFit({ key: `providers-${Date.now()}`, points: [loc.coords, ...list] });
    } else backToIdle();
    continueDialog(myTurn, out, sayReply(out));
  };

  // Hands-free: listening stops by itself when the driver pauses (tap to stop early).
  const beginListening = async (fromWake = false) => {
    ensureSession();
    live.current = true;
    setPhase("listening");
    clearTimers();
    const myTurn = ++turn.current;
    let res: string;
    try {
      res = await voice.listen({ chime: !fromWake }); // the wake word plays its own chime
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
    pauseDialog();
    voice.stopSpeaking();
    clearTimers();
    turn.current++; // discard any in-flight turn
    inflight.current?.abort();
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
    setDriving(false);
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
          continueDialog(myTurn, res, spoken);
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
        onLayout={setSheetH}
        wakePhrase={wake.active ? wake.phrase : null}
        panel={providers ? <ProvidersList data={providers} onRoute={(p) => onPlacePress(providerPlace(p))} /> : null}
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
  recenter: { position: "absolute", right: spacing.lg },
  recenterBtn: {
    width: 56,
    height: 56,
    borderRadius: 28,
    alignItems: "center",
    justifyContent: "center",
  },
});
