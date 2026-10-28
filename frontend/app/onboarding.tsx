import { useRouter } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import { Platform, ScrollView, StyleSheet, Switch, Text, TextInput, View } from "react-native";
import { KeyboardAwareScrollView, KeyboardStickyView } from "@/src/keyboard";
import Animated, { FadeIn, FadeInDown } from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { absoluteUrl, api } from "@/src/api";
import { Avatar } from "@/src/components/Avatar";
import { Icon } from "@/src/components/Icon";
import { MicPermissionCard } from "@/src/components/MicPermissionCard";
import { PressableScale } from "@/src/components/PressableScale";
import { Waveform } from "@/src/components/Waveform";
import { saveTruckProfile, setOnboarded, useTruckProfile, type TruckProfile } from "@/src/hooks/useTruckProfile";
import { useVoice, type MicPermission } from "@/src/hooks/useVoice";
import { fonts, radius, spacing, useTheme } from "@/src/theme";

type Step = "name" | "truck" | "prefs" | "done";
type Mode = "speaking" | "listening" | "thinking" | "waiting";
const STEPS: Step[] = ["name", "truck", "prefs", "done"];
const CHAINS = ["Pilot", "Flying J", "Love's", "TA", "Petro", "Sapp Bros", "Kwik Trip", "Buc-ee's"];
const BREAKS: (number | null)[] = [null, 2, 3, 4, 6, 8];
const NOT_CAUGHT = "Sorry, I didn't catch that.";

const toInt = (s: string) => parseInt(s.replace(/[^0-9]/g, ""), 10);
// Rough speaking time, used as a fallback when audio can't autoplay.
const speechMs = (text: string) => Math.min(20000, 1500 + text.length * 65);

/** What the copilot says out loud. */
function spoken(step: Step, name: string) {
  const who = name ? `, ${name}` : "";
  switch (step) {
    case "name":
      return "Hi! I'm TruckBuddy, your copilot on the road. Let's get to know each other. What's your name?";
    case "truck":
      return `Nice to meet you${who}! Now tell me about your rig: height, trailer length, weight, and whether you haul hazmat.`;
    case "prefs":
      return "Here are a few things I can keep in mind on every route. If any of these matter to you, just say so and I'll remember.";
    default:
      return `All set${who}! Tap me anytime and tell me where you need to go.`;
  }
}

/** The short "thought" shown next to the avatar: only the question. */
function thought(step: Step, name: string) {
  switch (step) {
    case "name":
      return "What's your name?";
    case "truck":
      return "Tell me about your rig";
    case "prefs":
      return "Anything here matter to you?";
    default:
      return name ? `All set, ${name}!` : "All set!";
  }
}

function Num({ label, value, onChange, unit, testID }: { label: string; value: string; onChange: (s: string) => void; unit: string; testID: string }) {
  const { colors } = useTheme();
  return (
    <View style={styles.field}>
      <Text style={[styles.label, { color: colors.onSurfaceSecondary }]}>{label}</Text>
      <View style={[styles.inputRow, { backgroundColor: colors.surfaceSecondary }]}>
        <TextInput testID={testID} value={value} onChangeText={onChange} keyboardType="number-pad" style={[styles.input, { color: colors.onSurface }]} />
        <Text style={[styles.unit, { color: colors.muted }]}>{unit}</Text>
      </View>
    </View>
  );
}

function ToggleRow({ title, value, onChange, testID }: { title: string; value: boolean; onChange: (v: boolean) => void; testID: string }) {
  const { colors } = useTheme();
  return (
    <PressableScale testID={`${testID}-row`} onPress={() => onChange(!value)} style={[styles.toggle, { backgroundColor: colors.surfaceSecondary }]}>
      <Text style={[styles.toggleTitle, { color: colors.onSurface }]}>{title}</Text>
      <Switch testID={testID} value={value} onValueChange={onChange} trackColor={{ true: colors.info, false: colors.surfaceTertiary }} />
    </PressableScale>
  );
}

function Chip({ label, on, onPress, testID }: { label: string; on: boolean; onPress: () => void; testID: string }) {
  const { colors } = useTheme();
  return (
    <PressableScale
      testID={testID}
      onPress={onPress}
      accessibilityState={{ selected: on }}
      aria-selected={on}
      style={[styles.chip, { backgroundColor: on ? colors.info : colors.surface, borderColor: on ? colors.info : colors.border }]}
    >
      <Text style={[styles.chipText, { color: on ? colors.onInfo : colors.onSurface }]}>{label}</Text>
    </PressableScale>
  );
}

export default function Onboarding() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const saved = useTruckProfile();
  const voice = useVoice();

  const [step, setStep] = useState<Step>("name");
  const [mode, setMode] = useState<Mode>("waiting");
  const [typing, setTyping] = useState(false);
  const [draft, setDraft] = useState<TruckProfile>(saved);
  const [feet, setFeet] = useState(String(Math.floor(saved.height_in / 12)));
  const [inches, setInches] = useState(String(saved.height_in % 12));
  const [trailer, setTrailer] = useState(String(saved.trailer_ft));
  const [weight, setWeight] = useState(String(saved.weight_lb));
  const [heard, setHeard] = useState<string | null>(null);
  const [mic, setMic] = useState<Exclude<MicPermission, "granted"> | null>(null);
  const [truckHeard, setTruckHeard] = useState(false);

  const run = useRef(0); // bumps to cancel the running conversation turn
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const micOk = useRef<boolean | null>(null);

  const name = (draft.driver_name ?? "").trim();
  const idx = STEPS.indexOf(step);

  // Saved profile arrives async; prefill.
  useEffect(() => {
    setDraft(saved);
    setFeet(String(Math.floor(saved.height_in / 12)));
    setInches(String(saved.height_in % 12));
    setTrailer(String(saved.trailer_ft));
    setWeight(String(saved.weight_lb));
  }, [saved]);

  const set = (u: Partial<TruckProfile>) => setDraft((d) => ({ ...d, ...u }));

  const truckFrom = (f: string, i: string, t: string, w: string): Partial<TruckProfile> | null => {
    const h = toInt(f) * 12 + toInt(i || "0");
    const tr = toInt(t);
    const wt = toInt(w);
    if (!(h >= 60 && h <= 203 && toInt(i || "0") <= 11 && tr >= 10 && tr <= 65 && wt >= 5000 && wt <= 200000)) return null;
    return { height_in: h, trailer_ft: tr, weight_lb: wt };
  };
  const truckOk = truckFrom(feet, inches, trailer, weight) !== null;

  const say = async (text: string, token: number) => {
    setMode("speaking");
    try {
      const r = await api.speak(text);
      if (run.current !== token) return;
      await voice.playAndWait(absoluteUrl(r.audio_url), speechMs(text));
    } catch {
      await new Promise((r) => setTimeout(r, 600));
    }
  };

  const finish = useCallback(
    async (data: TruckProfile) => {
      run.current++;
      voice.stopSpeaking();
      voice.cancelRecording().catch(() => {});
      await saveTruckProfile({ ...data, driver_name: data.driver_name?.trim() || null });
      await setOnboarded();
      if (router.canGoBack()) router.back();
      else router.replace("/");
    },
    [router, voice],
  );

  const applyUpdates = (u: Record<string, unknown>) => {
    const next = { ...u } as Partial<TruckProfile>;
    if (Array.isArray(next.favorite_chains)) {
      next.favorite_chains = Array.from(new Set([...(draftRef.current.favorite_chains ?? []), ...next.favorite_chains]));
    }
    if (typeof next.height_in === "number") {
      setFeet(String(Math.floor(next.height_in / 12)));
      setInches(String(next.height_in % 12));
    }
    if (typeof next.trailer_ft === "number") setTrailer(String(next.trailer_ft));
    if (typeof next.weight_lb === "number") setWeight(String(next.weight_lb));
    const merged = { ...draftRef.current, ...next };
    draftRef.current = merged;
    setDraft(merged);
    // Warm the voice for the next lines so the copilot answers without a pause.
    if (typeof next.driver_name === "string" && next.driver_name.trim()) {
      const n = next.driver_name.trim();
      api.speak(spoken("truck", n)).catch(() => {});
      api.speak(spoken("done", n)).catch(() => {});
    }
  };

  const advance = (from: Step) => {
    setHeard(null);
    setTyping(false); // each new question starts as a conversation again
    setStep(STEPS[STEPS.indexOf(from) + 1]);
  };

  /** One conversation turn: speak, listen, understand, move on. */
  const converse = async (s: Step, token: number, listenOnly = false) => {
    if (!listenOnly) await say(spoken(s, (draftRef.current.driver_name ?? "").trim()), token);
    if (run.current !== token) return;
    if (s === "done") {
      await new Promise((r) => setTimeout(r, 700));
      if (run.current === token) finish(draftRef.current);
      return;
    }
    for (let attempt = 0; attempt < 2; attempt++) {
      if (micOk.current === null) {
        const p = await voice.micPermission();
        micOk.current = p === "granted";
        if (!micOk.current) {
          if (p === "ask") setMic("ask");
          else setTyping(true);
          setMode("waiting");
          return;
        }
      }
      if (!micOk.current) {
        setTyping(true);
        setMode("waiting");
        return;
      }
      setMode("listening");
      const res = await voice.listen().catch(() => "silent" as const);
      if (run.current !== token || res === "cancelled") return;
      if (res === "silent") {
        setMode("waiting");
        setHeard("Tap me to answer, or type it.");
        return;
      }
      setMode("thinking");
      try {
        const out = await api.onboardingParse(res, s);
        if (run.current !== token) return;
        const got = Object.keys(out.updates).length > 0;
        const saidNone = s === "prefs" && /\b(no|none|nothing|nope|that's it|all good)\b/i.test(out.transcript);
        if (got || saidNone) {
          applyUpdates(out.updates);
          if (s === "truck") setTruckHeard(true);
          setHeard(`“${out.transcript}”`);
          setMode("waiting");
          await new Promise((r) => setTimeout(r, 1100));
          if (run.current !== token) return;
          if (s === "truck" && !truckFrom(feetRef.current, inchesRef.current, trailerRef.current, weightRef.current)) {
            setTyping(true); // let the driver fix odd numbers by hand
            return;
          }
          advance(s);
          return;
        }
      } catch {
        // fall through to retry
      }
      if (run.current !== token) return;
      setHeard(NOT_CAUGHT);
      if (attempt === 0) await say(`${NOT_CAUGHT} ${thought(s, "")}`, token);
    }
    if (run.current === token) setMode("waiting");
  };

  // Latest field values for the async turn.
  const feetRef = useRef(feet);
  const inchesRef = useRef(inches);
  const trailerRef = useRef(trailer);
  const weightRef = useRef(weight);
  feetRef.current = feet;
  inchesRef.current = inches;
  trailerRef.current = trailer;
  weightRef.current = weight;

  // Start a conversation turn whenever the step changes.
  useEffect(() => {
    const token = ++run.current;
    voice.cancelRecording().catch(() => {});
    converse(step, token);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

  useEffect(
    () => {
      // Pre-generate the fixed lines while the first question is being asked.
      api.speak(spoken("prefs", "")).catch(() => {});
      return () => {
        run.current++;
      };
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const stopTurn = () => {
    run.current++;
    voice.stopSpeaking();
    voice.cancelRecording().catch(() => {});
    setMode("waiting");
  };

  const onType = () => {
    stopTurn();
    setTyping(true);
    setHeard(null);
  };

  const onAvatar = () => {
    if (step === "done") return;
    if (mode === "listening") {
      // Done talking: stop early, the turn picks it up.
      voice.finishListening();
      return;
    }
    if (mode === "thinking") return;
    voice.stopSpeaking();
    setTyping(false);
    setHeard(null);
    if (micOk.current === false) micOk.current = null;
    const token = ++run.current;
    converse(step, token, true); // tap = answer now, no need to repeat the question
  };

  const onNext = () => {
    stopTurn();
    setHeard(null);
    if (step === "truck") {
      const t = truckFrom(feet, inches, trailer, weight);
      if (!t) return;
      applyUpdates(t);
    }
    if (step === "done") {
      finish(draftRef.current);
      return;
    }
    advance(step);
  };

  const onSkip = () => finish({ ...draftRef.current, ...(truckFrom(feet, inches, trailer, weight) ?? {}) });

  const onAllowMic = async () => {
    const p = await voice.requestMic();
    setMic(p === "granted" ? null : "blocked");
    micOk.current = p === "granted";
    if (p === "granted") {
      const token = ++run.current;
      converse(step, token);
    }
  };

  const nextDisabled = (step === "name" && !name) || (step === "truck" && !truckOk);
  const chains = draft.favorite_chains ?? [];
  const status =
    mode === "listening" ? "Listening… just talk" : mode === "thinking" ? "Thinking…" : heard ?? "";

  return (
    <View style={[styles.root, { backgroundColor: colors.surface }]} testID="onboarding-screen">
      <View style={[styles.header, { paddingTop: insets.top + spacing.sm }]}>
        <View style={styles.dots}>
          {STEPS.map((s, i) => (
            <View key={s} style={[styles.dot, { backgroundColor: i <= idx ? colors.info : colors.surfaceTertiary, width: i === idx ? 24 : 8 }]} />
          ))}
        </View>
        {step !== "done" && (
          <PressableScale testID="onboarding-skip-button" onPress={onSkip} style={styles.skip}>
            <Text style={[styles.skipText, { color: colors.onSurfaceSecondary }]}>Skip</Text>
          </PressableScale>
        )}
      </View>

      <KeyboardAwareScrollView
        bottomOffset={120}
        contentContainerStyle={[styles.content, !typing && step !== "prefs" && styles.centered]}
        keyboardShouldPersistTaps="handled"
      >
        <View style={[styles.agent, typing && styles.agentRow]}>
          <Animated.View key={step} entering={FadeIn.duration(320)} style={[styles.thoughtWrap, typing && styles.flexShrink]}>
            <View style={[styles.thought, typing && styles.thoughtSmall, { backgroundColor: colors.surfaceSecondary }]}>
              <Text testID="onboarding-agent-line" style={[styles.thoughtText, typing && styles.thoughtTextSmall, { color: colors.onSurface }]}>
                {thought(step, name)}
              </Text>
            </View>
            {!typing && <View style={[styles.puff1, { backgroundColor: colors.surfaceSecondary }]} />}
            {!typing && <View style={[styles.puff2, { backgroundColor: colors.surfaceSecondary }]} />}
          </Animated.View>

          <PressableScale testID="onboarding-avatar-button" accessibilityLabel="Talk to copilot" onPress={onAvatar}>
            <Avatar size={typing ? 64 : 150} mode={mode === "listening" ? "listening" : mode === "thinking" ? "thinking" : voice.speaking ? "speaking" : "idle"} showMic={false} />
          </PressableScale>

          {!typing && (
            <View style={styles.statusBox}>
              {mode === "listening" ? <Waveform /> : null}
              <Text testID="onboarding-heard" numberOfLines={2} style={[styles.status, { color: colors.onSurfaceSecondary }]}>
                {status}
              </Text>
            </View>
          )}
        </View>

        {step === "truck" && !typing && (
          <Text testID="onboarding-truck-summary" style={[styles.summaryLine, { color: truckHeard ? colors.onSurface : colors.muted }]}>
            {truckHeard
              ? `${feet} ft ${inches} in · ${trailer} ft trailer · ${Number(toInt(weight) || 0).toLocaleString()} lb${draft.hazmat ? " · HAZMAT" : ""}`
              : "Say it like: “13 foot 6, 53 foot trailer, 80,000 pounds, no hazmat”"}
          </Text>
        )}

        {/* The preferences are a list the driver can just talk about (or tap). */}
        {step === "prefs" && (
          <Animated.View entering={FadeInDown.duration(260)} style={styles.body}>
            <ToggleRow title="Avoid toll roads" value={!!draft.avoid_tolls} onChange={(v) => set({ avoid_tolls: v })} testID="pref-avoid-tolls-switch" />
            <ToggleRow title="Avoid ferries and tunnels" value={!!draft.avoid_ferries_tunnels} onChange={(v) => set({ avoid_ferries_tunnels: v })} testID="pref-avoid-ferries-switch" />
            <Text style={[styles.section, { color: colors.onSurface }]}>Favorite truck stops</Text>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips}>
              {CHAINS.map((c) => (
                <Chip
                  key={c}
                  label={c}
                  on={chains.includes(c)}
                  testID={`pref-chain-${c.replace(/[^a-z]/gi, "").toLowerCase()}`}
                  onPress={() => set({ favorite_chains: chains.includes(c) ? chains.filter((x) => x !== c) : [...chains, c] })}
                />
              ))}
            </ScrollView>
            <Text style={[styles.section, { color: colors.onSurface }]}>Break reminder</Text>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chips}>
              {BREAKS.map((b) => (
                <Chip key={String(b)} label={b ? `Every ${b} h` : "Off"} on={(draft.break_hours ?? null) === b} testID={`pref-break-${b ?? "off"}`} onPress={() => set({ break_hours: b })} />
              ))}
            </ScrollView>
          </Animated.View>
        )}

        {typing && step === "name" && (
          <Animated.View entering={FadeInDown.duration(220)} style={[styles.inputRow, { backgroundColor: colors.surfaceSecondary }]}>
            <TextInput
              testID="onboarding-name-input"
              value={draft.driver_name ?? ""}
              onChangeText={(v) => set({ driver_name: v.slice(0, 40) })}
              placeholder="Your first name"
              placeholderTextColor={colors.muted}
              autoCapitalize="words"
              autoFocus
              returnKeyType="next"
              onSubmitEditing={() => !nextDisabled && onNext()}
              style={[styles.input, { color: colors.onSurface }]}
            />
          </Animated.View>
        )}

        {typing && step === "truck" && (
          <Animated.View entering={FadeInDown.duration(220)} style={styles.body}>
            <View style={styles.row}>
              <View style={styles.flex}>
                <Num label="Height" value={feet} onChange={setFeet} unit="ft" testID="onboarding-height-feet-input" />
              </View>
              <View style={styles.flex}>
                <Num label=" " value={inches} onChange={setInches} unit="in" testID="onboarding-height-inches-input" />
              </View>
            </View>
            <Num label="Trailer length" value={trailer} onChange={setTrailer} unit="ft" testID="onboarding-trailer-input" />
            <Num label="Gross weight" value={weight} onChange={setWeight} unit="lb" testID="onboarding-weight-input" />
            <ToggleRow title="Hazardous materials" value={draft.hazmat} onChange={(v) => set({ hazmat: v })} testID="onboarding-hazmat-switch" />
            {!truckOk && (
              <Text testID="onboarding-truck-error" style={[styles.error, { color: colors.error }]}>
                Height 5–16 ft 11 in, trailer 10–65 ft, weight 5,000–200,000 lb.
              </Text>
            )}
          </Animated.View>
        )}
      </KeyboardAwareScrollView>

      {step !== "done" && (
        <KeyboardStickyView offset={{ opened: insets.bottom }}>
          <View style={[styles.footer, { paddingBottom: insets.bottom + spacing.lg, backgroundColor: colors.surface }]}>
            {typing || step === "prefs" ? (
              <PressableScale
                testID="onboarding-next-button"
                onPress={onNext}
                disabled={nextDisabled}
                containerStyle={styles.flex}
                style={[styles.next, { backgroundColor: nextDisabled ? colors.surfaceTertiary : colors.brandPrimary }]}
              >
                <Text style={[styles.nextText, { color: nextDisabled ? colors.muted : colors.onBrandPrimary }]}>
                  {step === "prefs" ? "Save preferences" : "Next"}
                </Text>
              </PressableScale>
            ) : (
              <PressableScale
                testID="onboarding-type-button"
                onPress={onType}
                containerStyle={styles.flex}
                style={[styles.typeBtn, { borderColor: colors.border }]}
              >
                <Icon name="keyboard-outline" size={22} color={colors.onSurface} />
                <Text style={[styles.typeText, { color: colors.onSurface }]}>Type instead</Text>
              </PressableScale>
            )}
          </View>
        </KeyboardStickyView>
      )}

      {mic && <MicPermissionCard state={mic} bottom={insets.bottom + 104} onAllow={onAllowMic} onDismiss={() => { setMic(null); setTyping(true); }} />}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: spacing.lg, minHeight: 56 },
  dots: { flexDirection: "row", gap: 6, alignItems: "center" },
  dot: { height: 8, borderRadius: 4 },
  skip: { minHeight: 44, justifyContent: "center", paddingHorizontal: spacing.sm },
  skipText: { fontFamily: fonts.semibold, fontSize: 15 },
  content: { flexGrow: 1, paddingHorizontal: spacing.lg, paddingBottom: spacing.xl, gap: spacing.lg },
  centered: { justifyContent: "center" },
  agent: { alignItems: "center", gap: spacing.md, marginTop: spacing.sm },
  thoughtWrap: { alignItems: "center", maxWidth: 320 },
  thought: { borderRadius: 28, paddingHorizontal: spacing.xl, paddingVertical: spacing.lg },
  thoughtText: { fontFamily: fonts.bold, fontSize: 22, lineHeight: 28, textAlign: "center" },
  puff1: { width: 16, height: 16, borderRadius: 8, marginTop: 6, marginLeft: 36 },
  puff2: { width: 9, height: 9, borderRadius: 5, marginTop: 4, marginLeft: 52 },
  agentRow: { flexDirection: "row-reverse", justifyContent: "flex-end", gap: spacing.md },
  flexShrink: { flexShrink: 1 },
  thoughtSmall: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderRadius: 20 },
  thoughtTextSmall: { fontSize: 17, lineHeight: 22, textAlign: "left" },
  statusBox: { minHeight: 64, alignItems: "center", justifyContent: "center", gap: spacing.sm },
  status: { fontFamily: fonts.medium, fontSize: 15, textAlign: "center" },
  summaryLine: { fontFamily: fonts.semibold, fontSize: 15, textAlign: "center" },
  body: { gap: spacing.md },
  row: { flexDirection: "row", gap: spacing.md },
  flex: { flex: 1 },
  field: { gap: 6 },
  label: { fontFamily: fonts.medium, fontSize: 13 },
  inputRow: { flexDirection: "row", alignItems: "center", height: 56, borderRadius: radius.md, paddingHorizontal: spacing.lg, overflow: "hidden" },
  input: {
    flex: 1,
    minWidth: 0,
    width: 0,
    height: "100%",
    fontFamily: fonts.semibold,
    fontSize: 18,
    ...(Platform.OS === "web" ? ({ outlineStyle: "none" } as object) : {}),
  },
  unit: { fontFamily: fonts.medium, fontSize: 15, marginLeft: spacing.sm, flexShrink: 0 },
  toggle: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: spacing.md, borderRadius: radius.md, paddingHorizontal: spacing.lg, minHeight: 56 },
  toggleTitle: { fontFamily: fonts.semibold, fontSize: 16, flexShrink: 1 },
  section: { fontFamily: fonts.bold, fontSize: 15, marginTop: spacing.xs },
  chips: { gap: spacing.sm, paddingRight: spacing.lg },
  chip: { flexShrink: 0, height: 44, borderRadius: radius.pill, borderWidth: 1.5, paddingHorizontal: spacing.lg, justifyContent: "center" },
  chipText: { fontFamily: fonts.semibold, fontSize: 14 },
  error: { fontFamily: fonts.medium, fontSize: 13 },
  footer: { flexDirection: "row", alignItems: "center", gap: spacing.md, paddingHorizontal: spacing.lg, paddingTop: spacing.sm },
  next: { height: 56, borderRadius: radius.md, alignItems: "center", justifyContent: "center" },
  nextText: { fontFamily: fonts.bold, fontSize: 16 },
  typeBtn: { height: 56, borderRadius: radius.md, borderWidth: 1.5, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: spacing.sm },
  typeText: { fontFamily: fonts.semibold, fontSize: 16 },
});
