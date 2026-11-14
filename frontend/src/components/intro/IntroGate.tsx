import { useCallback, useEffect, useRef, useState, type ComponentType, type ElementRef } from "react";
import { BackHandler, ScrollView, StyleSheet, useWindowDimensions, View, type NativeScrollEvent, type NativeSyntheticEvent } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { useT } from "../../i18n";
import { Text } from "../../i18n/Text";
import { markIntroSeen } from "../../state/intro";
import { fonts, radius, spacing, useTheme } from "../../theme";
import { PressableScale } from "../PressableScale";
import { IntroAsk } from "./IntroAsk";
import { IntroLot } from "./IntroLot";
import { IntroTalk } from "./IntroTalk";

const PAGES: ComponentType[] = [IntroTalk, IntroAsk, IntroLot];
const LAST = PAGES.length - 1;
/** How long a scroll started by a button may take before the pager listens to scroll events again anyway. */
const FLIGHT_MS = 600;

/**
 * What the app is, in three screens, between the language choice and the consent screen (Figma
 * f5Uha6gopQHRmcGlMXdCD9, B1-B3). Swipe or "Next"; "Skip" from the second screen on; Android's Back goes one screen
 * back. Either way out marks the intro seen (src/state/intro.ts) and the root layout moves on; it is never shown
 * again until "Delete my data".
 */
export function IntroGate({ onDone }: { onDone?: () => void }) {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const tr = useT();
  const { width } = useWindowDimensions();
  const pager = useRef<ElementRef<typeof ScrollView>>(null);
  const [page, setPage] = useState(0);
  const left = useRef(false);
  // A scroll started by a button (or Back) is "in flight" until the pager reaches the target. Meanwhile `page` is
  // already the target, and the scroll events on the way there, which still round to the old screen, are ignored:
  // the dots, "Skip" and the button label do not flicker back, and a second tap cannot run ahead of the picture
  // (a double tap on screen 2 used to finish the intro without showing screen 3, review of #169).
  const flight = useRef<{ to: number; timer: ReturnType<typeof setTimeout> } | null>(null);
  const land = useCallback(() => {
    if (flight.current) clearTimeout(flight.current.timer);
    flight.current = null;
  }, []);
  useEffect(() => land, [land]);

  const finish = () => {
    if (left.current) return; // a second tap while the root is still switching screens
    left.current = true;
    markIntroSeen().catch(() => {});
    onDone?.();
  };
  const goTo = useCallback(
    (to: number) => {
      land();
      flight.current = { to, timer: setTimeout(land, FLIGHT_MS) };
      setPage(to);
      pager.current?.scrollTo({ x: to * width, animated: true });
    },
    [land, width],
  );
  const next = () => {
    if (flight.current) return;
    if (page >= LAST) finish();
    else goTo(page + 1);
  };
  const pageAt = (e: NativeSyntheticEvent<NativeScrollEvent>) => Math.min(LAST, Math.max(0, Math.round(e.nativeEvent.contentOffset.x / width)));
  const onScroll = (e: NativeSyntheticEvent<NativeScrollEvent>) => {
    if (flight.current) {
      if (Math.abs(e.nativeEvent.contentOffset.x - flight.current.to * width) < 1) land();
      return;
    }
    const at = pageAt(e);
    if (at !== page) setPage(at);
  };
  // Where the pager came to rest is the truth, whoever moved it.
  const onRest = (e: NativeSyntheticEvent<NativeScrollEvent>) => {
    land();
    const at = pageAt(e);
    if (at !== page) setPage(at);
  };

  // Android's Back: one screen back; on the first screen the system has it (the app goes to the background).
  useEffect(() => {
    const sub = BackHandler.addEventListener("hardwareBackPress", () => {
      if (page === 0) return false;
      if (!flight.current) goTo(page - 1);
      return true;
    });
    return () => sub.remove();
  }, [page, goTo]);

  return (
    <View testID="intro-gate" style={[styles.root, { backgroundColor: colors.surface, paddingTop: insets.top, paddingBottom: insets.bottom + spacing.xl }]}>
      {/* The row keeps its height on the first screen, where there is no "Skip", so the screens line up. */}
      <View style={styles.header}>
        {page > 0 && (
          <PressableScale testID="intro-skip" accessibilityRole="button" accessibilityLabel={tr("Skip")} onPress={finish} style={styles.skip}>
            <Text style={[styles.skipText, { color: colors.info }]}>Skip</Text>
          </PressableScale>
        )}
      </View>
      <ScrollView
        ref={pager}
        testID="intro-pager"
        horizontal
        pagingEnabled
        bounces={false}
        showsHorizontalScrollIndicator={false}
        onScroll={onScroll}
        onScrollBeginDrag={land} // the driver's finger takes over from a button's scroll
        onMomentumScrollEnd={onRest}
        scrollEventThrottle={16}
        style={styles.pager}
      >
        {PAGES.map((Page, i) => (
          // A screen reader gets the screen in view only, top to bottom, then the dots and the button.
          <View
            key={i}
            testID={`intro-page-${i + 1}`}
            style={{ width }}
            accessibilityElementsHidden={i !== page}
            importantForAccessibility={i === page ? "auto" : "no-hide-descendants"}
          >
            <Page />
          </View>
        ))}
      </ScrollView>
      <View style={styles.footer}>
        <View testID="intro-dots" accessible accessibilityRole="text" accessibilityLabel={tr("Page {n} of {total}", { n: page + 1, total: PAGES.length })} style={styles.dots}>
          {PAGES.map((_, i) => (
            <View key={i} testID={i === page ? "intro-dot-on" : "intro-dot"} style={[styles.dot, i === page ? { width: 24, backgroundColor: colors.brandPrimary } : { backgroundColor: colors.muted }]} />
          ))}
        </View>
        <PressableScale
          testID="intro-next"
          accessibilityRole="button"
          accessibilityLabel={page < LAST ? tr("Next") : tr("Continue")}
          onPress={next}
          style={[styles.button, { backgroundColor: colors.brandPrimary }]}
        >
          <Text style={[styles.buttonText, { color: colors.onBrandPrimary }]}>{page < LAST ? "Next" : "Continue"}</Text>
        </PressableScale>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1 },
  header: { minHeight: 56, flexDirection: "row", justifyContent: "flex-end", paddingHorizontal: spacing.sm },
  // A text link with a 56 pt target (in-cab rule).
  skip: { minHeight: 56, minWidth: 56, alignItems: "center", justifyContent: "center", paddingHorizontal: spacing.lg },
  skipText: { fontFamily: fonts.bold, fontSize: 17 },
  pager: { flex: 1 },
  footer: { paddingHorizontal: spacing.xl, gap: spacing.lg },
  dots: { flexDirection: "row", gap: spacing.sm, justifyContent: "center", alignItems: "center", height: 8 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  button: { height: 56, borderRadius: radius.pill, alignItems: "center", justifyContent: "center" },
  buttonText: { fontFamily: fonts.bold, fontSize: 17 },
});
