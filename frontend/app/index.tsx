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
import { NavBanner } from "@/src/components/NavBanner";
import { PressableScale } from "@/src/components/PressableScale";
import { dial, ProvidersList } from "@/src/components/ProvidersList";
import { CaptionBubble, Toast, type ToastData } from "@/src/components/Toast";
import { TopBar } from "@/src/components/TopBar";
import { MESSAGES } from "@/src/constants";
import { useDriverLocation } from "@/src/hooks/useDriverLocation";
import { useGuidance } from "@/src/hooks/useGuidance";
import { getTruckProfile, isOnboarded, saveTruckProfile, setHandsFree, truckLabel, useHandsFree, useTruckProfile, type TruckProfile } from "@/src/hooks/useTruckProfile";
import { useVoice, type MicPermission } from "@/src/hooks/useVoice";
import { useWakeWord } from "@/src/hooks/useWakeWord";
import { shadow, spacing, useTheme } from "@/src/theme";
import type { ActiveRoute, AgentResponse, AppAction, LatLng, ParkingCheck, ParkingState, Phase, Place, Provider, ProvidersResult } from "@/src/types";
import { storage } from "@/src/utils/storage";
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
const STAGES = ["Searching nearby…", "Setting route…"];
const STAGE_MS = 3500;
const WAKE_SLEEP_MS = 10 * 60 * 1000; // background without a route: turn the wake word off
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
    setPhase(activeRef.current ? "navigating" : providersRef.current ? "providers" : optionsRef.current.length ? "options" : "ready");

  // Turn-by-turn while driving; prompts stay quiet while the driver is talking to the copilot.
  const guidance = useGuidance(active, loc.coords, driving, voiceOn, (text) => {
    if (phase === "listening" || phase === "thinking" || voice.speaking) return; // never cut off the copilot
    setCaption({ reply: text });
    api
      .speak(text)
      .then((r) => voice.play(absoluteUrl(r.audio_url)))
      .catch(() => {});
  });

  const onGo = () => {
    endDialog();
    voice.stopSpeaking();
    setCaption(null);
    setDriving(true);
  };

  /** Typed request: same pipeline as voice, without recording or reopening the mic. */
  const onSubmitText = (text: string) => {
    if (phase === "thinking") return;
    voice.stopSpeaking();
    voice.cancelRecording().catch(() => {});
    endDialog();
    setCaption(null);
    submitTurn(undefined, text);
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
      const res = await api.agentSpeak(transcript, loc.coords, activeRef.current, ctrl.signal, dialog.current ?? undefined, {
        driving,
        voice_guidance: voiceOn,
        hands_free: handsFree,
        options_count: optionsRef.current.length,
      });
      if (myTurn !== turn.current) return;
      // Fast answer: the filler never starts. Otherwise the answer waits for it to finish.
      filler?.cancel();
      const afterFiller = filler?.done ?? Promise.resolve();
      const speak = () => afterFiller.then(() => (myTurn === turn.current ? sayReply(res) : undefined));
      if (res.dial_phone) dial(res.dial_phone);
      if (res.status === "researching" && res.research_id) {
        await afterFiller;
        await runResearch(res, myTurn, ctrl.signal);
        return;
      }
      if (res.status === "route_set" && res.place && res.route) {
        const others = res.options ?? [];
        setOptions(others);
        setParking({});
        setPlaces([res.place, ...others]);
        startRoute({ place: res.place, route: res.route, parking: res.parking });
        const replyDone = speak();
        followParking(res.place, replyDone, myTurn, res.route.approach);
        endDialog(); // route is set: stop listening until the driver taps the mic again
      } else {
        if (res.status !== "chat" && res.status !== "not_understood") showToast(res.message ?? MESSAGES.notUnderstood);
        backToIdle();
        if (res.actions?.length) runActions(res.actions, myTurn); // after backToIdle: actions may change the phase
        const spoken = speak();
        const followup = res.actions?.find((a) => a.type === "parking_followup");
        const fPlace = followup && [activeRef.current?.place, ...optionsRef.current, ...places].find((p) => p?.id === followup.value);
        const answered = fPlace
          ? parkingFollowup(fPlace, spoken, {
              isCurrent: () => myTurn === turn.current,
              busy: () => phaseRef.current === "listening" || phaseRef.current === "thinking",
              say: async (text, url) => {
                setCaption({ reply: text });
                const a = toAudio(url);
                if (a) await voice.playAndWait(a, 6000);
              },
            })
          : spoken;
        continueDialog(myTurn, res, answered);
      }
    } catch (e) {
      filler?.cancel();
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
    const out = await api.breakdownResult(res.research_id!, signal);
    done = true;
    clearTimeout(interim);
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
    if (!dialog.current) dialog.current = `dlg-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    setPhase("listening");
    clearTimers();
    const myTurn = ++turn.current;
    let res: string;
    try {
      res = await voice.listen({ silenceMs: 1100, chime: !fromWake }); // the wake word plays its own chime
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
  const wakeArmed = wake.supported && handsFree && micOk && !asleep;
  useEffect(() => {
    if (!wakeArmed) wake.shutdown();
    else if (focused && idle) wake.start();
    else wake.stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wakeArmed, focused, idle]);
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
    endDialog();
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
  const reroute = async (myTurn: number) => {
    const cur = activeRef.current;
    if (!cur) return;
    try {
      const res = await api.route(loc.coords, cur.place);
      // Only the destination must still match: a newer turn shouldn't keep the old route on screen.
      if (activeRef.current?.place.id === cur.place.id) {
        setActive((a) => (a ? { ...a, route: res.route } : a));
      }
    } catch {
      if (myTurn === turn.current) showToast("Couldn't update the route. Try again.");
    }
  };

  /** Executes app controls the copilot returned (trip, sound, map, truck data, preferences). */
  const runActions = (actions: AppAction[], myTurn: number) => {
    let truckPatch: Partial<TruckProfile> = {};
    for (const { type, value } of actions) {
      switch (type) {
        case "start_navigation":
          if (activeRef.current) {
            endDialog();
            setDriving(true);
          }
          break;
        case "end_route":
          endDialog();
          clearRoute();
          break;
        case "show_options":
          if (optionsRef.current.length) setPhase("options");
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
        case "zoom_in":
          mapRef.current?.zoomBy(1);
          break;
        case "zoom_out":
          mapRef.current?.zoomBy(-1);
          break;
        case "voice_guidance":
          setVoiceOn(value === true);
          break;
        case "wake_word":
          setHandsFree(value === true);
          break;
        case "stop_talking":
          endDialog();
          break;
        case "set_height_in":
          truckPatch.height_in = Number(value);
          break;
        case "set_trailer_ft":
          truckPatch.trailer_ft = Number(value);
          break;
        case "set_weight_lb":
          truckPatch.weight_lb = Number(value);
          break;
        case "set_hazmat":
          truckPatch.hazmat = value === true;
          break;
        case "set_driver_name":
          truckPatch.driver_name = String(value);
          break;
        case "avoid_tolls":
          truckPatch.avoid_tolls = value === true;
          break;
        case "avoid_ferries_tunnels":
          truckPatch.avoid_ferries_tunnels = value === true;
          break;
        case "add_favorite_chain": {
          const list = truckPatch.favorite_chains ?? getTruckProfile().favorite_chains ?? [];
          truckPatch.favorite_chains = [...list, String(value)];
          break;
        }
        case "remove_favorite_chain": {
          const list = truckPatch.favorite_chains ?? getTruckProfile().favorite_chains ?? [];
          truckPatch.favorite_chains = list.filter((c) => c.toLowerCase() !== String(value).toLowerCase());
          break;
        }
        case "set_break_hours":
          truckPatch.break_hours = value == null ? null : Number(value);
          break;
        case "reroute":
          // Save first so the new route uses the new profile.
          saveTruckProfile({ ...getTruckProfile(), ...truckPatch }).then(() => reroute(myTurn));
          truckPatch = {};
          break;
      }
    }
    if (Object.keys(truckPatch).length) saveTruckProfile({ ...getTruckProfile(), ...truckPatch });
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
        onSearch={(q) => onSubmitText(`Take me to ${q}`)}
        onTruckPress={() => router.push("/truck-profile")}
        onHistoryPress={() => router.push("/history")}
        isDemo={loc.resolved && loc.isDemo}
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
