import { useFocusEffect, useRouter } from "expo-router";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Keyboard, StyleSheet, useWindowDimensions, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { absoluteUrl, api } from "@/src/api";
import { useCopilotTurn, type AnswerContext } from "@/src/dialog/useCopilotTurn";
import { turnContext } from "@/src/dialog/turnContext";
import { useFillers } from "@/src/dialog/useFillers";
import { bubbleRoom, expireLine, hasExpiringLine, isReplyCaption, LINE_HOLD_MS, lineCaption, replyCaption, replyPlacement, type Caption } from "@/src/dialog/caption";
import { FLOATING_SIZE, floatingBottom } from "@/src/state/floating";
import { routeFitPoints } from "@/src/map/bounds";
import { AlternativesSheet } from "@/src/components/AlternativesSheet";
import { CopilotSheet } from "@/src/components/CopilotSheet";
import { FollowButton } from "@/src/components/FollowButton";
import { MapViewSheet } from "@/src/components/MapViewSheet";
import { MapViewButton, RadarStatus } from "@/src/components/RadarControls";
import { useRadar } from "@/src/hooks/useRadar";
import { STEP_MIN as STEP_MIN_SLIDER, TimeSlider } from "@/src/components/TimeSlider";
import { frameClock, radarOverlay } from "@/src/map/mapView";
import { chipAt, layerAt, positionAt, shareAt, sliderVisible, trackParts } from "@/src/weather/timeSlider";
import { nearestIndex } from "@/src/weather/routeWeather";
import { useRouteWeather } from "@/src/hooks/useRouteWeather";
import { WeatherPointSheet, spokenLine } from "@/src/components/WeatherPointSheet";
import { useRouteWeatherStore } from "@/src/state/routeWeatherStore";
import { chips as weatherChipsOf, type Chip } from "@/src/weather/routeWeather";
import { MAP_PROVIDER } from "@/src/map/provider";
import MapLayer from "@/src/components/MapLayer";
import type { MapLayerHandle } from "@/src/components/mapTypes";
import { MicPermissionCard } from "@/src/components/MicPermissionCard";
import { HeightConfirmCard } from "@/src/components/HeightConfirmCard";
import { HowToSheet } from "@/src/components/HowToSheet";
import { useHowToFlow } from "@/src/hooks/useHowToFlow";
import { useEldToken } from "@/src/eld";
import { confirmHeight, isHeightConfirmed, useHeightConfirmed } from "@/src/truck/heightConfirmed";
import { NavBanner } from "@/src/components/NavBanner";
import { dial, ProvidersList } from "@/src/components/ProvidersList";
import { RestoredBanner } from "@/src/components/RestoredBanner";
import { CaptionBubble, Toast } from "@/src/components/Toast";
import { ConfirmSheet } from "@/src/components/ConfirmSheet";
import { TopBar } from "@/src/components/TopBar";
import { MESSAGES } from "@/src/constants";
import { isTester } from "@/src/auth";
import { switchedLine } from "@/src/geo/routeChoice";
import { farFromRoute } from "@/src/geo/guidance";
import { useApproachTips } from "@/src/hooks/useApproachTips";
import { useAppActive } from "@/src/hooks/useAppActive";
import { useBreakReminder } from "@/src/hooks/useBreakReminder";
import { useDriveRecorder } from "@/src/hooks/useDriveRecorder";
import { useDriverLocation } from "@/src/hooks/useDriverLocation";
import { useDrivingSession } from "@/src/hooks/useDrivingSession";
import { useGuidance, type Guidance } from "@/src/hooks/useGuidance";
import { useOfflineGuidance, type OfflineGuidance } from "@/src/hooks/useOfflineGuidance";
import { useStartPins, useWarmCaches } from "@/src/hooks/useNearbyWarmup";
import { useOfflineMap } from "@/src/hooks/useOfflineMap";
import { isOnline, useOnline } from "@/src/hooks/useOnline";
import { useRouteAdvisories } from "@/src/hooks/useRouteAdvisories";
import { useFollowCamera } from "@/src/hooks/useFollowCamera";
import { useHardwareBack } from "@/src/hooks/useHardwareBack";
import { useReroute } from "@/src/hooks/useReroute";
import { useMicPrewarm } from "@/src/hooks/useMicPrewarm";
import { NUDGE_KEY_PREFIX, useNudges } from "@/src/hooks/useNudges";
import { useSpeechQueue } from "@/src/hooks/useSpeechQueue";
import { useToast } from "@/src/hooks/useToast";
import { useTripRestore } from "@/src/hooks/useTripRestore";
import { saveProfile } from "@/src/data/profile";
import { getTruckProfile, isOnboarded, truckLabel, useTruckProfile } from "@/src/hooks/useTruckProfile";
import { useVoice, type MicPermission } from "@/src/hooks/useVoice";
import { askWithDeadline } from "@/src/voice/micAsk";
import { spacing, useTheme } from "@/src/theme";
import { providerPlace, selectPlaces, useTripStore } from "@/src/state/tripStore";
import { setHandsFree, setMapView, setNudgesEnabled, setVoiceGuidance, takeTruckReroute, useHandsFree, useMapView, useVoiceGuidance } from "@/src/state/settingsStore";
import { reduceAgentResponse } from "@/src/state/agentReducer";
import { applyEffects } from "@/src/state/applyEffects";
import { offlineTurn } from "@/src/state/offlineTurn";
import type { ActiveRoute, AgentResponse, LatLng, Phase, Place } from "@/src/types";
import { PRIORITY } from "@/src/voice/queue";
import { playReply } from "@/src/voice/playReply";
import { entrySpot, routeEndsAt } from "@/src/utils/entry";
import { parkingFollowup, toAudio } from "@/src/utils/parkingFollowup";
import { routeFailText } from "@/src/utils/routeFail";
import { searchRequest } from "@/src/utils/searchRequest";
import { verdictLine } from "@/src/voice/parkingLines";
import { recorder } from "@/src/geo/driveRecorder";
import { tripLog } from "@/src/net/tripEvents";
import { t } from "@/src/i18n";

// "End route" asks first while more than this is left of the drive.
const END_CONFIRM_ABOVE_S = 10 * 60;

const RESEARCH_INTERIM = "Reading reviews now, almost there.";
const RESEARCH_INTERIM_MS = 7000;

const ROUTE_STAGE = "Setting route…";
// The trip (route, driving, stops, options, providers, parking, sheet) lives in src/state/tripStore.ts;
// async handlers read `trip()` at the moment they need a value instead of a ref mirror of a render.
const trip = useTripStore.getState;

export default function MapScreen() {
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const loc = useDriverLocation();
  const voice = useVoice();
  const truck = useTruckProfile();
  const heightConfirmed = useHeightConfirmed();
  const router = useRouter();
  const mapRef = useRef<MapLayerHandle>(null);
  const center = useRef<LatLng>(loc.coords);

  const [topH, setTopH] = useState(140);
  // Map / Satellite / Weather (Figma "Weather v2"). The MapLibre build has no imagery: there satellite falls back to the map.
  const satelliteOk = MAP_PROVIDER !== "maplibre";
  const storedView = useMapView();
  const autoWeather = useRouteWeatherStore((st) => st.autoWeather);
  const mapView0 = storedView === "satellite" && !satelliteOk ? "map" : storedView;
  const mapView = autoWeather && mapView0 === "map" ? "weather" : mapView0;
  const [mapViewOpen, setMapViewOpen] = useState(false);
  const radar = useRadar(mapView === "weather");
  const [wxChip, setWxChip] = useState<Chip | null>(null);
  const [sheetH, setSheetH] = useState(260);
  const { height: windowH } = useWindowDimensions();
  // The sheet's peek height while the driver has pulled it down; the map keeps sheetH as its inset
  // (no camera refit on a drag) but the chrome above the sheet moves down into the freed room.
  const [peekH, setPeekH] = useState<number | null>(null);
  // The map the chrome leaves visible (small at 130 % font); a lowered sheet frees room for the bubble.
  const mapWindow = windowH - topH - (peekH ?? sheetH);
  const chromeBottom = (peekH ?? sheetH) + spacing.lg;
  // Cards and the caption on the left also clear the map's attribution logo, which sits right above the sheet.
  const cardBottom = chromeBottom + spacing.lg;
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
  const { toast, showToast } = useToast();
  // kind "reply" = the copilot's answer, kept until the driver dismisses it or the next answer comes (a driver
  // who looks later must still see it); kind "line" = a spoken line (turn, nudge, parking tip), gone 8 s after
  // it is said. A line during an answer shows as a note under it (src/dialog/caption.ts).
  const [caption, setCaption] = useState<Caption | null>(null);
  /** A spoken line's caption; it never replaces an answer. */
  const showLine = (text: string) => setCaption((prev) => lineCaption(text, prev));
  const [micCard, setMicCard] = useState<Exclude<MicPermission, "granted"> | null>(null);
  /** Drawn heights of the answer bubble and of the card on the map (height, mic): 0 when gone. */
  const [bubbleH, setBubbleH] = useState(0);
  const [cardH, setCardH] = useState(0);
  // Whether this screen is the one on top (not under the truck profile or history).
  const [focused, setFocused] = useState(true);
  /** The answer that did not fit whole in the bubble, and the room it had: it stays in the sheet until a new
   *  answer comes or the bubble gets more room (the driver lowered the sheet). */
  const [overflow, setOverflow] = useState<{ reply: string; room: number } | null>(null);
  /** The "End the route to …?" question while it is up (ConfirmSheet), null otherwise. */
  const [endConfirm, setEndConfirm] = useState<string | null>(null);
  const [fit, setFit] = useState<{ key: string; points: LatLng[] }>({ key: "init", points: [] });
  const [thinkingText, setThinkingText] = useState<string | undefined>(undefined);
  const setDriving = (v: boolean) => trip().setDriving(v);
  // The list of the other routes the engine offered (the "Routes (N)" chip, before "Go").
  const [routesOpen, setRoutesOpen] = useState(false);
  // Follow truck, or the whole route for a moment while driving (src/hooks/useFollowCamera.ts).
  const { following, setOverview, setTracking } = useFollowCamera(fit.key, driving);
  const voiceOn = useVoiceGuidance();
  const setVoiceOn = setVoiceGuidance;
  const { online } = useOnline();

  const searchBlurGuard = useRef(false); // while set, "Where to?" gives a focus it gets back (see onSubmitText)

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
  useDriveRecorder(active, driving, clearSpeech, () => {
    setDialogPhase(null);
    setDriving(true);
  });

  // A spoken line (or the note under an answer) goes LINE_HOLD_MS after it was said; the answer itself stays.
  useEffect(() => {
    if (!hasExpiringLine(caption) || voice.speaking) return;
    const t = setTimeout(() => setCaption((prev) => expireLine(prev)), LINE_HOLD_MS);
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
  useBreakReminder(active, truck, say);

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

  // Route preview: the answer bubble sits on the map right above the sheet, so the map's bottom padding takes
  // it in and the route refits above it; the destination pin is never under the bubble (UI review 2026-11-05,
  // M11). Dismissing the bubble gives the room back.
  const previewing = phase === "navigating" && !!active && !driving;
  // "How to use" (src/state/howTo.ts): opens by itself once after onboarding, or from "?" and the sheet's link. A chip
  // is a typed request, the same path as the sheet's text field; "Try it now" is a tap on the avatar. Both handlers
  // are declared further down and only called on a tap, after this render.
  const busy = phase === "listening" || phase === "thinking";
  const howTo = useHowToFlow({ focused, micCard: !!micCard, busy, ask: (q) => onSubmitText(q), listen: () => onAvatarPress() });
  const motiveKey = useEldToken();
  // The height card waits while the mic card asks, and while the copilot listens or thinks (one thing at a time).
  // After onboarding "How to use" goes first and the height card right after it: the card stays until answered
  // and Go waits for it, so it loses nothing by waiting, while two things on screen at once is what the
  // one-at-a-time rule is for. Until the how-to flag is read the card waits too, or it would flash for a frame.
  const showHeightCard =
    heightConfirmed === false && focused && !micCard && !busy && !howTo.holdsCards;
  const cardUp = (showHeightCard || !!micCard) && cardH > 0;
  // The bubble sits above a card on the map, never under it: under the height card the whole answer was hidden
  // (UI review 2026-11-05, third pass, M2).
  const bubbleBottom = cardUp ? cardBottom + cardH + spacing.sm : cardBottom;
  const room = bubbleRoom(mapWindow, bubbleBottom - (peekH ?? sheetH));
  const overflowed = caption?.kind === "reply" && overflow?.reply === caption.reply && room <= overflow.room;
  const replyPlace = replyPlacement({ mapWindow, room, overflowed, sheetHosts: phase === "ready" || previewing });
  const bubbleUp = !!caption && replyPlace === "map" && !routesOpen;
  const onBubbleFit = useCallback(
    (fits: boolean) => {
      if (!fits && caption?.kind === "reply") setOverflow({ reply: caption.reply, room });
    },
    [caption, room],
  );
  // Follow truck sits above the sheet (or above a card), and only where it clears the top bar's buttons.
  const followBottom = floatingBottom({ windowH, topH, bottom: cardUp ? cardBottom + cardH + spacing.sm : chromeBottom });
  // The map view button sits above Follow truck, and only when it too clears the top bar.
  const viewBottom = followBottom === null ? null : floatingBottom({ windowH, topH, bottom: followBottom + FLOATING_SIZE + spacing.md });
  const mapBottom = sheetH + (previewing && bubbleUp && bubbleH > 0 ? bubbleH + 2 * spacing.lg : 0);
  const previewFit = previewing && active ? `preview-${active.place.id}-${mapBottom}` : null;
  const [previewFitSeen, setPreviewFitSeen] = useState(previewFit);
  if (previewFit !== previewFitSeen) {
    setPreviewFitSeen(previewFit);
    if (previewFit && active) setFit({ key: previewFit, points: [loc.coords, active.place] });
  }
  // While the driver picks a route, the other routes are on the map too (grey, under the blue current one) and
  // the camera takes them all in: choosing between lines nobody can see was the M1 of the third review pass.
  const altLines = useMemo(
    () => (routesOpen && previewing && active?.route.alternatives?.length ? active.route.alternatives.map((r) => r.geometry) : undefined),
    [routesOpen, previewing, active],
  );
  const altFit = altLines && active ? `routes-${active.place.id}-${altLines.length}-${sheetH}` : null;
  const [altFitSeen, setAltFitSeen] = useState(altFit);
  if (altFit !== altFitSeen) {
    setAltFitSeen(altFit);
    if (altFit && altLines && active) setFit({ key: altFit, points: [loc.coords, active.place, ...routeFitPoints([active.route.geometry, ...altLines])] });
    else if (previewFit && active) setFit({ key: `${previewFit}-back`, points: [loc.coords, active.place] });
  }

  const sayReply = (res: AgentResponse, isCurrent: () => boolean = () => true) => playReply(voice, setCaption, res, isCurrent);

  const fillers = useFillers(voice.playAndWait);

  // Warm Places + parking caches around the driver; the truck stops around him as pins on an empty map.
  const appActive = useAppActive();
  useWarmCaches(loc, appActive, active);
  useStartPins(loc, { active, phase, optionsCount: options.length, providers, placesCount: places.length }, phaseRef, setFit);

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
      const text = verdictLine(pk);
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

  const restored = useTripRestore(active, driving, restoredId, setDialogPhase, setFit);

  // Where the truck is now, for handlers that run after an await.
  const coordsRef = useRef<LatLng>(loc.coords);
  useEffect(() => {
    coordsRef.current = loc.coords;
  }, [loc.coords]);
  const offlineRef = useRef<OfflineGuidance | null>(null);
  // Off-route reroute, re-plan with the truck profile or to the entrance, and "×" on a stop (src/hooks/useReroute.ts).
  const { rerouteOffRoute, reroute, onRemoveStop } = useReroute(loc, coordsRef, offlineRef, say, showToast);
  // One object per GPS fix (not per render): useGuidance pushes each fix into the off-route tracker once.
  const fix = useMemo(
    () => ({ ...loc.coords, speed: loc.speed, accuracy: loc.accuracy, ts: loc.ts }),
    [loc.coords, loc.speed, loc.accuracy, loc.ts],
  );
  const guidance = useGuidance(active, fix, driving, voiceOn, say, rerouteOffRoute, (next, stage, toNextM) => {
    // The "now" call is the one that can come late: under 60 m at 65 mph is less than two seconds.
    if (stage === "now") tripLog.log("maneuver_said", { action: next.action, index: next.index, to_next_m: Math.round(toNextM) }, trip().active?.trip_id);
  });

  // Half a mile out, once per destination: the entry and parking tips from the card.
  useApproachTips(driving, active, guidance.remainingM, say);

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
    context: () =>
      turnContext(trip(), {
        coords: coordsRef.current, // the truck moved while the driver talked and the words were transcribed
        voiceOn: voiceOnRef.current,
        handsFree: handsFreeRef.current,
        heading: locRef.current.heading,
        speed: locRef.current.speed,
        limitMph: limitRef.current,
        nextInstruction: guidanceRef.current.next?.instruction,
      }),
    onPhase: setPhase,
    onIdle: () => backToIdle(),
    onThinking: setThinkingText,
    onHeard: (transcript) => setCaption({ reply: "…", heard: transcript }),
    onNotUnderstood: () => {
      // Walkie-talkie: every tap that heard nothing says so, with its own sound (not the "sent" one).
      voice.cue("miss");
      if (!captionRef.current) setCaption({ reply: MESSAGES.notUnderstood });
    },
    onError: (message) => setCaption(replyCaption(message)),
    onListenFailed: () => showToast(MESSAGES.notUnderstood),
    onResponse: (res, ctx) => applyResponse(res, ctx),
  });
  // A nudge just played (the speech queue's onDone, registered above): with autoListen a short mic window for
  // the "yes"; in walkie-talkie mode nothing.
  useEffect(() => {
    nudgeAnswer.current = turn.nudgeOffer;
  });

  // ---- Nudges: the backend's offers every few minutes while driving (src/hooks/useNudges.ts). ----
  useNudges(driving, !!active, say, turn, guidanceRef, coordsRef);

  const onGo = () => {
    if (!isHeightConfirmed()) {
      // Bridges on the route were checked for the default 13'6"; a taller truck must not drive it blind.
      showToast("Tell me your truck height first: it decides which bridges are safe.", "info");
      return;
    }
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
    const interimAudio = api.speak(t(RESEARCH_INTERIM)).catch(() => null);
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
  useFocusEffect(
    useCallback(() => {
      setFocused(true);
      return () => setFocused(false);
    }, []),
  );

  // Recorder kept armed while this screen is in front; the mic card goes once the mic is allowed.
  useMicPrewarm(voice.setPrewarm, voice.micPermission, focused, micCard, appActive, setMicCard);

  const onAvatarPress = async () => {
    if (phase === "listening") {
      voice.finishListening();
      return;
    }
    // Barge-in: a tap while the copilot is still working drops that turn and listens again (same session,
    // so "no, the other one" lands where it should); useCopilotTurn does the dropping.
    voice.stopSpeaking();
    // The permission round trip only until it is known granted: the tap must start recording at once.
    if (!voice.micKnownGranted()) {
      const perm = await voice.micPermission();
      if (perm !== "granted") {
        setMicCard(perm);
        return;
      }
    }
    if (!turn.dialogId()) setCaption(null);
    turn.tap();
  };

  const openHowTo = () => {
    Keyboard.dismiss();
    howTo.open();
  };

  const onAllowMic = async () => {
    // A request that never answers (seen in Expo Go) must not leave the card dead: after 5 s the card
    // offers Settings, but a late "granted" still closes it (src/voice/micAsk.ts).
    const { perm, late } = await askWithDeadline<MicPermission>(() => voice.requestMic(), 5000, () => setMicCard("blocked"), "blocked");
    if (perm === "granted") {
      setMicCard(null);
      if (!late) turn.tap();
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

  /** The End route button. Mid-trip it asks first: one stray tap ended a 2.5-hour trip with no way back
   *  (QA 2026-11-02). Near the end, or before Go, it ends at once. */
  const onEndRoutePress = () => {
    const name = trip().active?.place.name.split(",")[0];
    if (!trip().driving || guidance.remainingS < END_CONFIRM_ABOVE_S) return onEndRoute();
    setEndConfirm(name ? t("End the route to {name}?", { name }) : t("End the route?"));
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

  // Hardware Back closes the top layer (src/hooks/useHardwareBack.ts).
  useHardwareBack(
    {
      state: { micCard: !!micCard, routesOpen, reply: isReplyCaption(caption), phase },
      cancel: () => {
        turn.cancel();
        voice.cancelRecording().catch(() => {});
        turn.pause();
        backToIdle();
      },
      closeOptions: onClose,
      endRoute: onEndRoute,
    },
    setMicCard,
    setRoutesOpen,
    setCaption,
  );

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

  // Background location, keep-awake, the fleet heartbeat and arrival events while driving.
  useDrivingSession(driving, active, loc);

  // Resume of a restored route: the first real fix after it decides whether the truck is still near the line.
  // A parked truck never trips the off-route tracker, so far from it the plan is redone right away.
  // Checked at once when a real fix is already there (the truck parked: no new fix may come for a while),
  // else on the first one that arrives.
  const replanOnFix = useRef(false);
  const replanIfFar = () => {
    if (!replanOnFix.current || loc.isDemo) return;
    replanOnFix.current = false;
    const cur = trip().active;
    if (!cur || !trip().driving || !farFromRoute(cur.route.geometry, loc.coords)) return;
    say("Rerouting.", { priority: PRIORITY.MANEUVER, kind: "system", key: "reroute", ttlMs: 20000 });
    reroute("resume");
  };
  useEffect(() => {
    replanIfFar();
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
      saveProfile: (patch) => {
        if (typeof patch.height_in === "number") confirmHeight(); // "my truck is 14 feet": an answer
        return saveProfile({ ...getTruckProfile(), ...patch });
      },
      unknownAction: (action) => tripLog.log("turn_unknown_action", { action }, trip().active?.trip_id),
      // Save first so the new route uses the new profile.
      reroute: (patch) => {
        if (typeof patch.height_in === "number") confirmHeight();
        return saveProfile({ ...getTruckProfile(), ...patch }).then(() => reroute("profile"));
      },
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

  // Weather along the active route at the truck's ETA (Figma "Weather v2"): chips on the map, a line in the card.
  useRouteWeather(active?.route, truck.trailer_empty, coordsRef);
  const wxPoints = useRouteWeatherStore((st) => st.points);
  const wxFetchedAt = useRouteWeatherStore((st) => st.fetchedAt);
  const wxChips = useMemo(() => (active ? weatherChipsOf(wxPoints, active.route) : []), [wxPoints, active]);
  // The time slider (Figma V4): weather view, a route set, not driving (FMCSA: no dragging on the move).
  const sliderOn = sliderVisible({ mapView, hasRoute: !!active, driving, phase });
  // From where the truck is (a route restored mid-trip starts there, not at the origin): the time still ahead.
  const sliderFrom = active && sliderOn ? shareAt(active.route.geometry as [number, number][], nearestIndex(active.route.geometry as [number, number][], loc.coords)) : 0;
  const sliderTotal = active ? Math.max(STEP_MIN_SLIDER, Math.round((active.route.duration_s / 60) * (1 - sliderFrom))) : 0;
  const [slider, setSlider] = useState<{ key: string; min: number; now: Date }>({ key: "", min: 0, now: new Date() });
  const sliderKey = active ? `${active.place.id}:${active.route.duration_s}:${mapView}` : "";
  if (slider.key !== sliderKey) setSlider({ key: sliderKey, min: 0, now: new Date() });
  const sliderAt = new Date(slider.now.getTime() + slider.min * 60000);
  const sliderAhead = sliderOn && slider.min > 0;
  // Past the HRRR horizon there is no picture: no radar at all (never the live one under a future time).
  const sliderLayer = sliderAhead ? layerAt(radar.frame, sliderAt, slider.now) : null;
  const sliderOverlay = sliderAhead ? (sliderLayer ? radarOverlay(radar.frame, absoluteUrl(""), sliderLayer) : null) : radar.overlay;
  const ghostAt = sliderAhead && active ? positionAt(active.route.geometry as [number, number][], sliderFrom + (1 - sliderFrom) * (slider.min / sliderTotal)) : null;
  const ghost = ghostAt ? { ...ghostAt, label: frameClock(sliderAt.toISOString()) ?? "" } : null;
  useEffect(() => {
    if (ghostAt) mapRef.current?.centerOn(ghostAt);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slider.min]);

  return (
    <View style={[styles.root, { backgroundColor: colors.mapPlaceholder }]} testID="map-screen">
      <MapLayer
        ref={mapRef}
        driver={loc.coords}
        heading={loc.heading}
        places={places}
        // While the driver picks from the list (before Go), the map shows the choices only: the last preview's
        // route and "Harbor Truck…" label stayed over the list (UI review 2026-11-05, M16). Its pin stays as one
        // of the options.
        active={phase === "options" && !driving ? null : active}
        alternatives={altLines}
        topInset={topH}
        bottomInset={mapBottom}
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
        mapView={mapView}
        // Offline the tiles can't load and the cached ones are old: the radar is drawn faint (Figma V8).
        radar={online ? sliderOverlay : sliderOverlay && { ...sliderOverlay, opacity: 0.35 }}
        ghost={ghost}
        weatherChips={wxChips}
        onWeatherChipPress={setWxChip}
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
        // While the driver picks from the list, the pill must not say "To: <the first pick>" over it.
        destination={phase === "options" ? null : (active?.place.name ?? null)}
        onSearch={(q) => onSubmitText(searchRequest(q))}
        blurGuard={searchBlurGuard}
        onTruckPress={() => router.push("/truck-profile")}
        onHelpPress={openHowTo}
        helpDot={howTo.dot}
        onHistoryPress={() => router.push("/history")}
        tester={isTester()}
        simLabel={loc.sim?.label ?? null}
        onTesterPress={() => router.push("/tester")}
        isDemo={loc.resolved && loc.isDemo}
        onLocationPress={loc.enable}
        onLayout={setTopH}
      />
      )}

      {/* Hidden when the map window has no room for it under the top bar: it sat on the history button
          (UI review 2026-11-05, third pass, M3). */}
      {mapView === "weather" && !(driving && active) && <RadarStatus top={topH + spacing.sm} frame={radar.frame} forecastAt={sliderAhead ? sliderAt : null} beyondForecast={sliderAhead && !sliderLayer} offlineSince={online ? null : wxFetchedAt} />}
      {viewBottom !== null && <MapViewButton bottom={viewBottom} active={mapView !== "map"} onPress={() => setMapViewOpen(true)} />}
      <WeatherPointSheet
        chip={wxChip}
        onClose={() => setWxChip(null)}
        onRead={(c) => say(spokenLine(c), { priority: PRIORITY.WEATHER, kind: "system", key: `wx-${c.index}`, ttlMs: 30000 })}
      />
      <MapViewSheet
        visible={mapViewOpen}
        value={mapView}
        satellite={satelliteOk}
        onPick={(v) => {
          setMapView(v);
          setMapViewOpen(false);
        }}
        onClose={() => setMapViewOpen(false)}
      />
      {followBottom !== null && (
        <FollowButton
          following={following}
          bottom={followBottom}
          onPress={() => {
            if (following) {
              setTracking(false);
              if (driving) setOverview(true);
            } else {
              setOverview(false);
              setTracking(true);
            }
          }}
        />
      )}

      <CaptionBubble
        text={bubbleUp ? (caption?.reply ?? null) : null}
        heard={caption?.heard}
        note={caption?.note}
        bottom={bubbleBottom}
        maxHeight={room}
        sources={caption?.sources}
        onDismiss={() => setCaption(null)}
        onHeight={setBubbleH}
        onFit={onBubbleFit}
      />

      <CopilotSheet
        phase={phase}
        speaking={voice.speaking}
        micLevel={voice.level}
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
        lastReplySources={caption?.kind === "reply" ? caption.sources : undefined}
        onClose={onClose}
        onEndRoute={onEndRoutePress}
        onRemoveStop={onRemoveStop}
        onShowRoutes={() => setRoutesOpen(true)}
        offlineMap={offlineMap.state}
        onDownloadMap={offlineMap.download}
        onLayout={setSheetH}
        onPeekHeight={setPeekH}
        wakePhrase={null}
        quiet={showHeightCard || !!micCard}
        panel={providers ? <ProvidersList data={providers} onRoute={(p) => onPlacePress(providerPlace(p))} /> : null}
        weatherPanel={
          sliderOn && active ? (
            <TimeSlider
              totalMin={sliderTotal}
              value={Math.min(slider.min, sliderTotal)}
              onChange={(min) => setSlider((s) => ({ ...s, min }))}
              parts={trackParts(wxChips, slider.now, sliderTotal)}
              chip={chipAt(wxChips, sliderAt)}
              now={slider.now}
            />
          ) : null
        }
        routesPanel={
          routesOpen && active && !driving && !!active.route.alternatives?.length ? (
            <AlternativesSheet primary={active.route} onPick={onPickAlternative} onClose={() => setRoutesOpen(false)} />
          ) : null
        }
        replyInSheet={caption?.kind === "reply" && replyPlace === "sheet" ? caption.reply : null}
        onHowTo={openHowTo}
      />

      {showHeightCard && (
        <HeightConfirmCard
          bottom={cardBottom}
          current={truck.height_in}
          onPick={async (h) => {
            await confirmHeight();
            const changed = h !== getTruckProfile().height_in;
            await saveProfile({ ...getTruckProfile(), height_in: h });
            if (changed && trip().active) reroute("profile"); // the route was planned for another height
          }}
          onOther={() => router.push("/truck-profile")}
          onHeight={setCardH}
        />
      )}

      {micCard && (
        <MicPermissionCard
          state={micCard}
          bottom={cardBottom}
          onAllow={onAllowMic}
          onDismiss={() => setMicCard(null)}
          onHeight={setCardH}
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
            replanIfFar();
          }}
          onDismiss={clearRoute}
        />
      )}

      <Toast toast={toast} top={topH + spacing.sm} />

      <HowToSheet mode={howTo.mode} name={truck.driver_name} motive={!!motiveKey} onAsk={howTo.ask} onTryNow={howTo.tryNow} onClose={howTo.close} />

      <ConfirmSheet
        testID="end-route-confirm"
        title={endConfirm}
        confirmLabel="End route"
        cancelLabel="Keep driving"
        onConfirm={() => {
          setEndConfirm(null);
          onEndRoute();
        }}
        onCancel={() => setEndConfirm(null)}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
});
