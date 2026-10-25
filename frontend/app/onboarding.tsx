import { useRouter } from "expo-router";
import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Platform, ScrollView, StyleSheet, Switch, Text, TextInput, View } from "react-native";
import { KeyboardAwareScrollView, KeyboardStickyView } from "react-native-keyboard-controller";
import Animated, { FadeIn, FadeInDown } from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { absoluteUrl, api } from "@/src/api";
import { Avatar } from "@/src/components/Avatar";
import { Icon } from "@/src/components/Icon";
import { MicPermissionCard } from "@/src/components/MicPermissionCard";
import { PressableScale } from "@/src/components/PressableScale";
import { saveTruckProfile, setOnboarded, useTruckProfile, type TruckProfile } from "@/src/hooks/useTruckProfile";
import { useVoice, type MicPermission } from "@/src/hooks/useVoice";
import { fonts, radius, spacing, useTheme } from "@/src/theme";

type Step = "name" | "truck" | "prefs" | "done";
const STEPS: Step[] = ["name", "truck", "prefs", "done"];
const CHAINS = ["Pilot", "Flying J", "Love's", "TA", "Petro", "Sapp Bros", "Kwik Trip", "Buc-ee's"];
const BREAKS: (number | null)[] = [null, 2, 3, 4, 6, 8];

const toInt = (s: string) => parseInt(s.replace(/[^0-9]/g, ""), 10);

function line(step: Step, name: string) {
  const who = name ? `, ${name}` : "";
  switch (step) {
    case "name":
      return "Hi! I'm TruckBuddy, your copilot on the road. Let's get to know each other. What's your name?";
    case "truck":
      return `Nice to meet you${who}! Now tell me about your rig: height, trailer length, weight, and whether you haul hazmat.`;
    case "prefs":
      return "Here are a few things I can keep in mind on every route. If any of these matter to you, just say so and I'll remember.";
    default:
      return `All set${who}! Tap me anytime and tell me where you need to go. I'll plan every route for your truck.`;
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

function ToggleRow({ title, sub, value, onChange, testID }: { title: string; sub?: string; value: boolean; onChange: (v: boolean) => void; testID: string }) {
  const { colors } = useTheme();
  return (
    <View style={[styles.toggle, { backgroundColor: colors.surfaceSecondary }]}>
      <View style={styles.flex}>
        <Text style={[styles.toggleTitle, { color: colors.onSurface }]}>{title}</Text>
        {!!sub && <Text style={[styles.toggleSub, { color: colors.onSurfaceSecondary }]}>{sub}</Text>}
      </View>
      <Switch testID={testID} value={value} onValueChange={onChange} trackColor={{ true: colors.info, false: colors.surfaceTertiary }} />
    </View>
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
  const [draft, setDraft] = useState<TruckProfile>(saved);
  const [feet, setFeet] = useState(String(Math.floor(saved.height_in / 12)));
  const [inches, setInches] = useState(String(saved.height_in % 12));
  const [trailer, setTrailer] = useState(String(saved.trailer_ft));
  const [weight, setWeight] = useState(String(saved.weight_lb));
  const [rec, setRec] = useState<"idle" | "listening" | "thinking">("idle");
  const [heard, setHeard] = useState<string | null>(null);
  const [mic, setMic] = useState<Exclude<MicPermission, "granted"> | null>(null);
  const spokenFor = useRef<string>("");

  const name = (draft.driver_name ?? "").trim();
  const text = line(step, name);
  const idx = STEPS.indexOf(step);

  // Saved profile arrives async; prefill once.
  useEffect(() => {
    setDraft(saved);
    setFeet(String(Math.floor(saved.height_in / 12)));
    setInches(String(saved.height_in % 12));
    setTrailer(String(saved.trailer_ft));
    setWeight(String(saved.weight_lb));
  }, [saved]);

  // The copilot reads each step aloud.
  useEffect(() => {
    if (spokenFor.current === step) return;
    spokenFor.current = step;
    api
      .speak(text)
      .then((r) => voice.play(absoluteUrl(r.audio_url)))
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

  const set = (u: Partial<TruckProfile>) => setDraft((d) => ({ ...d, ...u }));

  const truckFromFields = (): Partial<TruckProfile> | null => {
    const f = toInt(feet);
    const i = toInt(inches || "0");
    const t = toInt(trailer);
    const w = toInt(weight);
    const h = f * 12 + i;
    if (!(h >= 60 && h <= 203 && i <= 11 && t >= 10 && t <= 65 && w >= 5000 && w <= 200000)) return null;
    return { height_in: h, trailer_ft: t, weight_lb: w };
  };
  const truckOk = truckFromFields() !== null;

  const applyUpdates = (u: Record<string, unknown>) => {
    const next = { ...u } as Partial<TruckProfile>;
    if (Array.isArray(next.favorite_chains)) {
      // A spoken answer adds to the chips already picked.
      next.favorite_chains = Array.from(new Set([...(draft.favorite_chains ?? []), ...next.favorite_chains]));
    }
    if (typeof next.height_in === "number") {
      setFeet(String(Math.floor(next.height_in / 12)));
      setInches(String(next.height_in % 12));
    }
    if (typeof next.trailer_ft === "number") setTrailer(String(next.trailer_ft));
    if (typeof next.weight_lb === "number") setWeight(String(next.weight_lb));
    set(next);
  };

  const finish = async (data: TruckProfile) => {
    voice.stopSpeaking();
    await saveTruckProfile({ ...data, driver_name: data.driver_name?.trim() || null });
    await setOnboarded();
    if (router.canGoBack()) router.back();
    else router.replace("/");
  };

  const onNext = () => {
    setHeard(null);
    if (rec !== "idle") {
      voice.cancelRecording().catch(() => {});
      setRec("idle");
    }
    if (step === "truck") {
      const t = truckFromFields();
      if (!t) return;
      set(t);
    }
    if (step === "done") {
      finish(draft);
      return;
    }
    setStep(STEPS[idx + 1]);
  };

  const onSkip = () => finish({ ...draft, ...(truckFromFields() ?? {}) });

  const startRec = async () => {
    try {
      await voice.startRecording();
      setHeard(null);
      setRec("listening");
    } catch {
      setHeard("Sorry, I couldn't start the microphone.");
    }
  };

  const onMic = async () => {
    if (step === "done" || rec === "thinking") return;
    if (rec === "listening") {
      setRec("thinking");
      try {
        const uri = await voice.stopRecording();
        if (!uri) throw new Error("no audio");
        const res = await api.onboardingParse(uri, step);
        if (Object.keys(res.updates).length) {
          applyUpdates(res.updates);
          setHeard(`Got it: “${res.transcript}”`);
        } else if (step === "prefs" && res.transcript) {
          setHeard(`Got it: “${res.transcript}” — no changes.`);
        } else {
          setHeard("Sorry, I didn't catch that. Try again or use the buttons.");
        }
      } catch {
        setHeard("Sorry, I didn't catch that. Try again or use the buttons.");
      } finally {
        setRec("idle");
      }
      return;
    }
    voice.stopSpeaking();
    const perm = await voice.micPermission();
    if (perm !== "granted") {
      setMic(perm);
      return;
    }
    startRec();
  };

  const onAllowMic = async () => {
    const perm = await voice.requestMic();
    if (perm === "granted") {
      setMic(null);
      startRec();
    } else setMic("blocked");
  };

  const nextDisabled = (step === "name" && !name.trim()) || (step === "truck" && !truckOk);
  const chains = draft.favorite_chains ?? [];

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

      <KeyboardAwareScrollView bottomOffset={120} contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <View style={styles.agent}>
          <Avatar size={104} mode={rec === "listening" ? "listening" : rec === "thinking" ? "thinking" : voice.speaking ? "speaking" : "idle"} showMic={false} />
          <Animated.View key={step} entering={FadeIn.duration(260)} style={[styles.bubble, { backgroundColor: colors.surfaceSecondary }]}>
            <Text testID="onboarding-agent-line" style={[styles.bubbleText, { color: colors.onSurface }]}>{text}</Text>
          </Animated.View>
        </View>

        <Animated.View key={`body-${step}`} entering={FadeInDown.duration(260)} style={styles.body}>
          {step === "name" && (
            <View style={[styles.inputRow, { backgroundColor: colors.surfaceSecondary }]}>
              <TextInput
                testID="onboarding-name-input"
                value={draft.driver_name ?? ""}
                onChangeText={(v) => set({ driver_name: v.slice(0, 40) })}
                placeholder="Your first name"
                placeholderTextColor={colors.muted}
                autoCapitalize="words"
                returnKeyType="next"
                onSubmitEditing={() => !nextDisabled && onNext()}
                style={[styles.input, { color: colors.onSurface }]}
              />
            </View>
          )}

          {step === "truck" && (
            <>
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
            </>
          )}

          {step === "prefs" && (
            <>
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
                  <Chip
                    key={String(b)}
                    label={b ? `Every ${b} h` : "Off"}
                    on={(draft.break_hours ?? null) === b}
                    testID={`pref-break-${b ?? "off"}`}
                    onPress={() => set({ break_hours: b })}
                  />
                ))}
              </ScrollView>
            </>
          )}

          {step === "done" && (
            <View testID="onboarding-summary" style={[styles.summary, { backgroundColor: colors.surfaceSecondary }]}>
              <Text style={[styles.sumLine, { color: colors.onSurface }]}>
                {Math.floor(draft.height_in / 12)} ft {draft.height_in % 12} in · {draft.trailer_ft} ft trailer · {draft.weight_lb.toLocaleString()} lb
                {draft.hazmat ? " · HAZMAT" : ""}
              </Text>
              <Text style={[styles.sumSub, { color: colors.onSurfaceSecondary }]}>
                {[
                  draft.avoid_tolls && "No tolls",
                  draft.avoid_ferries_tunnels && "No ferries/tunnels",
                  chains.length ? chains.join(", ") : null,
                  draft.break_hours ? `Break every ${draft.break_hours} h` : null,
                ]
                  .filter(Boolean)
                  .join(" · ") || "No route preferences"}
              </Text>
            </View>
          )}

          {!!heard && (
            <Text testID="onboarding-heard" style={[styles.heard, { color: colors.onSurfaceSecondary }]}>
              {heard}
            </Text>
          )}
        </Animated.View>
      </KeyboardAwareScrollView>

      <KeyboardStickyView offset={{ opened: insets.bottom }}>
        <View style={[styles.footer, { paddingBottom: insets.bottom + spacing.lg, backgroundColor: colors.surface }]}>
          {step !== "done" && (
            <PressableScale
              testID="onboarding-mic-button"
              accessibilityLabel={rec === "listening" ? "Stop" : "Answer by voice"}
              onPress={onMic}
              style={[styles.mic, { backgroundColor: rec === "listening" ? colors.error : colors.surfaceSecondary }]}
            >
              {rec === "thinking" ? (
                <ActivityIndicator color={colors.onSurface} />
              ) : (
                <Icon name={rec === "listening" ? "stop" : "microphone"} size={26} color={rec === "listening" ? colors.onError : colors.info} />
              )}
            </PressableScale>
          )}
          <PressableScale
            testID="onboarding-next-button"
            onPress={onNext}
            disabled={nextDisabled}
            containerStyle={styles.flex}
            style={[styles.next, { backgroundColor: nextDisabled ? colors.surfaceTertiary : colors.brandPrimary }]}
          >
            <Text style={[styles.nextText, { color: nextDisabled ? colors.muted : colors.onBrandPrimary }]}>
              {step === "done" ? "Start driving" : step === "prefs" ? "Save preferences" : "Next"}
            </Text>
          </PressableScale>
        </View>
      </KeyboardStickyView>

      {mic && <MicPermissionCard state={mic} bottom={insets.bottom + 104} onAllow={onAllowMic} onDismiss={() => setMic(null)} />}
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
  content: { paddingHorizontal: spacing.lg, paddingBottom: spacing.xl, gap: spacing.lg },
  agent: { alignItems: "center", gap: spacing.md, marginTop: spacing.sm },
  bubble: { borderRadius: radius.lg, padding: spacing.lg, alignSelf: "stretch" },
  bubbleText: { fontFamily: fonts.semibold, fontSize: 18, lineHeight: 26, textAlign: "center" },
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
  toggle: { flexDirection: "row", alignItems: "center", gap: spacing.md, borderRadius: radius.md, paddingHorizontal: spacing.lg, minHeight: 60 },
  toggleTitle: { fontFamily: fonts.semibold, fontSize: 16 },
  toggleSub: { fontFamily: fonts.regular, fontSize: 13 },
  section: { fontFamily: fonts.bold, fontSize: 15, marginTop: spacing.xs },
  chips: { gap: spacing.sm, paddingRight: spacing.lg },
  chip: { flexShrink: 0, height: 44, borderRadius: radius.pill, borderWidth: 1.5, paddingHorizontal: spacing.lg, justifyContent: "center" },
  chipText: { fontFamily: fonts.semibold, fontSize: 14 },
  error: { fontFamily: fonts.medium, fontSize: 13 },
  summary: { borderRadius: radius.lg, padding: spacing.lg, gap: 6 },
  sumLine: { fontFamily: fonts.bold, fontSize: 16 },
  sumSub: { fontFamily: fonts.medium, fontSize: 14 },
  heard: { fontFamily: fonts.medium, fontSize: 14, textAlign: "center" },
  footer: { flexDirection: "row", alignItems: "center", gap: spacing.md, paddingHorizontal: spacing.lg, paddingTop: spacing.sm },
  mic: { width: 56, height: 56, borderRadius: 28, alignItems: "center", justifyContent: "center" },
  next: { height: 56, borderRadius: radius.md, alignItems: "center", justifyContent: "center" },
  nextText: { fontFamily: fonts.bold, fontSize: 16 },
});
