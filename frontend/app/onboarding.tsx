import { useRouter } from "expo-router";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { BackHandler, Platform, TextInput, View } from "react-native";
import { Text } from "@/src/i18n/Text";
import { KeyboardAwareScrollView, KeyboardStickyView } from "@/src/keyboard";
import Animated, { FadeIn, FadeInDown } from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { absoluteUrl, api } from "@/src/api";
import { Avatar } from "@/src/components/Avatar";
import { Icon } from "@/src/components/Icon";
import { MicPermissionCard } from "@/src/components/MicPermissionCard";
import { PrefsForm } from "@/src/components/PrefsForm";
import { OnboardingTruckForm } from "@/src/components/OnboardingTruckForm";
import { styles } from "@/src/components/onboardingStyles";
import { PressableScale } from "@/src/components/PressableScale";
import { Waveform } from "@/src/components/Waveform";
import { saveProfile } from "@/src/data/profile";
import { setOnboarded, useTruckProfile, type TruckProfile } from "@/src/hooks/useTruckProfile";
import { useVoice, type MicPermission } from "@/src/hooks/useVoice";
import { spacing, useTheme } from "@/src/theme";
import { truckErrors, truckFromInputs } from "@/src/truck/validate";
import { LANGS, setLang, t, UK_READY, useLang, type Lang } from "@/src/i18n";
import { confirmHeight } from "@/src/truck/heightConfirmed";
import { NOT_CAUGHT, spoken, STEPS, TAP_TO_TALK, thought, type Step } from "@/src/voice/onboardingLines";

type Mode = "speaking" | "listening" | "thinking" | "waiting";

const toInt = (s: string) => parseInt(s.replace(/[^0-9]/g, ""), 10);
// Rough speaking time, used as a fallback when audio can't autoplay.
const speechMs = (text: string) => Math.min(20000, 1500 + text.length * 65);

export default function Onboarding() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const lang = useLang();
  const { colors } = useTheme();
  const saved = useTruckProfile();
  const voice = useVoice();

  const [step, setStep] = useState<Step>("name");
  const [mode, setMode] = useState<Mode>("waiting");
  const [typing, setTyping] = useState(false);
  const typingRef = useRef(false); // read inside converse() after awaits
  useLayoutEffect(() => {
    typingRef.current = typing;
  }, [typing]);
  // Small avatar beside the line: while typing, and on the preferences step, whose form otherwise
  // pushed its last row under the Save button.
  const compact = typing || step === "prefs";
  const [draft, setDraft] = useState<TruckProfile>(saved);
  const [feet, setFeet] = useState(String(Math.floor(saved.height_in / 12)));
  const [inches, setInches] = useState(String(saved.height_in % 12));
  const [trailer, setTrailer] = useState(String(saved.trailer_ft));
  const [weight, setWeight] = useState(String(saved.weight_lb));
  const [heard, setHeard] = useState<string | null>(null);
  const [mic, setMic] = useState<Exclude<MicPermission, "granted"> | null>(null);
  const [truckHeard, setTruckHeard] = useState(false);

  const run = useRef(0); // bumps to cancel the running conversation turn
  const draftRef = useRef(draft); // for the async turn; written at commit, synchronously
  useLayoutEffect(() => {
    draftRef.current = draft;
  }, [draft]);
  const micOk = useRef<boolean | null>(null);

  const name = (draft.driver_name ?? "").trim();
  const idx = STEPS.indexOf(step);

  // Saved profile arrives async; prefill.
  const [prefilled, setPrefilled] = useState(saved);
  if (prefilled !== saved) {
    setPrefilled(saved);
    setDraft(saved);
    setFeet(String(Math.floor(saved.height_in / 12)));
    setInches(String(saved.height_in % 12));
    setTrailer(String(saved.trailer_ft));
    setWeight(String(saved.weight_lb));
  }

  const set = (u: Partial<TruckProfile>) => setDraft((d) => ({ ...d, ...u }));

  const truckFrom = (f: string, i: string, t: string, w: string): Partial<TruckProfile> | null =>
    truckFromInputs({ feet: f, inches: i, trailer: t, weight: w });
  const truckErr = truckErrors({ feet, inches, trailer, weight });
  const truckOk = Object.keys(truckErr).length === 0;

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
      await saveProfile({ ...data, driver_name: data.driver_name?.trim() || null });
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

  // Hardware Back: the previous question, not the launcher (the map screen does the same with its layers).
  const stepRef = useRef<Step>("name");
  useLayoutEffect(() => {
    stepRef.current = step;
  }, [step]);
  useEffect(() => {
    if (Platform.OS !== "android") return;
    const sub = BackHandler.addEventListener("hardwareBackPress", () => {
      const i = STEPS.indexOf(stepRef.current);
      if (i <= 0 || stepRef.current === "done") return false;
      run.current++;
      voice.stopSpeaking();
      voice.cancelRecording().catch(() => {});
      setHeard(null);
      setStep(STEPS[i - 1]);
      return true;
    });
    return () => sub.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const advance = (from: Step) => {
    setHeard(null);
    // The driver's choice of typing stays for the next question (QA 2026-10-28, U3); a tap on the
    // copilot brings the voice back at any step.
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
    if (typingRef.current && !listenOnly) {
      setMode("waiting"); // the driver chose to type: the question is read, the mic stays shut
      return;
    }
    for (let attempt = 0; attempt < 2; attempt++) {
      if (micOk.current === null) {
        const p = await voice.micPermission();
        micOk.current = p === "granted";
        if (!micOk.current) {
          // The permission card only after a tap on the copilot (U1: not a third dialog in the first
          // five seconds); until then the question waits with a hint.
          if (p === "ask" && listenOnly) setMic("ask");
          else if (p === "ask") setHeard(TAP_TO_TALK);
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
        const out = await api.onboardingParse(typeof res === "string" ? res : res.uri, s);
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
      // Each half in the driver's language: the glued English line had no entry and was spoken in English (S5).
      if (attempt === 0) await say(`${t(NOT_CAUGHT)} ${t(thought(s, ""))}`, token);
    }
    if (run.current === token) setMode("waiting");
  };

  // Latest field values for the async turn.
  const feetRef = useRef(feet);
  const inchesRef = useRef(inches);
  const trailerRef = useRef(trailer);
  const weightRef = useRef(weight);
  useLayoutEffect(() => {
    feetRef.current = feet;
    inchesRef.current = inches;
    trailerRef.current = trailer;
    weightRef.current = weight;
  }, [feet, inches, trailer, weight]);

  // Start a conversation turn whenever the step changes.
  useEffect(() => {
    const token = ++run.current;
    voice.cancelRecording().catch(() => {});
    converse(step, token);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

  useEffect(() => {
    // Pre-generate the fixed lines while the first question is being asked.
    api.speak(spoken("prefs", "")).catch(() => {});
    const turns = run; // a counter, not a node: bumping it on unmount cancels the running turn
    return () => {
      turns.current++;
    };
  }, []);

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
      confirmHeight(); // Next on the truck step is an answer; Skip is not (the map asks again)
    }
    if (step === "done") {
      finish(draftRef.current);
      return;
    }
    advance(step);
  };

  // The language the copilot speaks from here on: say the current question again in it.
  const onLang = (l: Lang) => {
    if (l === lang) return;
    setLang(l);
    stopTurn();
    setHeard(null);
    const token = ++run.current;
    setTimeout(() => converse(step, token), 0); // after the store update, so spoken() reads the new language
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
        {step === "name" && (
          <View style={styles.langRow}>
            {LANGS.filter((l) => l.code !== "uk" || UK_READY).map((l) => (
              <PressableScale key={l.code} testID={`onboarding-lang-${l.code}`} onPress={() => onLang(l.code)} style={styles.skip}>
                <Text style={[styles.skipText, { color: lang === l.code ? colors.info : colors.onSurfaceSecondary }]}>{l.label}</Text>
              </PressableScale>
            ))}
          </View>
        )}
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
        <View style={[styles.agent, compact && styles.agentRow]}>
          <Animated.View key={step} entering={FadeIn.duration(320)} style={[styles.thoughtWrap, compact && styles.flexShrink]}>
            <View style={[styles.thought, compact && styles.thoughtSmall, { backgroundColor: colors.surfaceSecondary }]}>
              <Text testID="onboarding-agent-line" style={[styles.thoughtText, compact && styles.thoughtTextSmall, { color: colors.onSurface }]}>
                {thought(step, name)}
              </Text>
            </View>
            {!compact && <View style={[styles.puff1, { backgroundColor: colors.surfaceSecondary }]} />}
            {!compact && <View style={[styles.puff2, { backgroundColor: colors.surfaceSecondary }]} />}
          </Animated.View>

          <PressableScale testID="onboarding-avatar-button" accessibilityLabel={t("Talk to copilot")} onPress={onAvatar}>
            <Avatar size={compact ? 64 : 150} mode={mode === "listening" ? "listening" : mode === "thinking" ? "thinking" : voice.speaking ? "speaking" : "idle"} showMic={false} />
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
              ? `${feet} ft ${inches} in · ${trailer} ft trailer · ${Number(toInt(weight) || 0).toLocaleString("en-US")} lb${draft.hazmat ? " · HAZMAT" : ""}`
              : "Say it like: “13 foot 6, 53 foot trailer, 80,000 pounds, no hazmat”"}
          </Text>
        )}

        {/* Two big answers: a tap is as good as saying it. */}
        {step === "work" && (
          <Animated.View entering={FadeInDown.duration(260)} style={styles.choices}>
            {([["company", "I drive for a company"], ["owner", "It's my own truck"]] as const).map(([value, label]) => (
              <PressableScale
                key={value}
                testID={`onboarding-operator-${value}`}
                onPress={() => {
                  stopTurn();
                  applyUpdates({ operator: value });
                  advance("work");
                }}
                style={[
                  styles.choice,
                  { borderColor: draft.operator === value ? colors.info : colors.border, backgroundColor: colors.surfaceSecondary },
                ]}
              >
                <Text style={[styles.choiceText, { color: colors.onSurface }]}>{t(label)}</Text>
              </PressableScale>
            ))}
          </Animated.View>
        )}

        {/* The preferences are a list the driver can just talk about (or tap). */}
        {step === "prefs" && (
          <Animated.View entering={FadeInDown.duration(260)}>
            <PrefsForm value={draft} onChange={set} />
          </Animated.View>
        )}

        {typing && step === "name" && (
          <Animated.View entering={FadeInDown.duration(220)} style={[styles.inputRow, { backgroundColor: colors.surfaceSecondary }]}>
            <TextInput
              testID="onboarding-name-input"
              value={draft.driver_name ?? ""}
              onChangeText={(v) => set({ driver_name: v.slice(0, 40) })}
              placeholder={t("Your first name")}
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
          <OnboardingTruckForm
            feet={feet}
            inches={inches}
            trailer={trailer}
            weight={weight}
            onFeet={setFeet}
            onInches={setInches}
            onTrailer={setTrailer}
            onWeight={setWeight}
            errors={truckErr}
            hazmat={draft.hazmat}
            onHazmat={(v) => set({ hazmat: v })}
          />
        )}
      </KeyboardAwareScrollView>

      {step !== "done" && step !== "work" && (
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
