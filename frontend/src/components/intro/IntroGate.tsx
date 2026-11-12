import { useRef, useState, type ComponentType, type ElementRef } from "react";
import { ScrollView, StyleSheet, useWindowDimensions, View, type NativeScrollEvent, type NativeSyntheticEvent } from "react-native";
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

/**
 * What the app is, in three screens, between the language choice and the consent screen (Figma
 * f5Uha6gopQHRmcGlMXdCD9, B1-B3). Swipe or "Next"; "Skip" from the second screen on. Either way out marks the intro
 * seen (src/state/intro.ts) and the root layout moves on; it is never shown again until "Delete my data".
 */
export function IntroGate({ onDone }: { onDone?: () => void }) {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const tr = useT();
  const { width } = useWindowDimensions();
  const pager = useRef<ElementRef<typeof ScrollView>>(null);
  const [page, setPage] = useState(0);
  const left = useRef(false);

  const finish = () => {
    if (left.current) return; // a second tap while the root is still switching screens
    left.current = true;
    markIntroSeen().catch(() => {});
    onDone?.();
  };
  const next = () => {
    if (page >= LAST) return finish();
    setPage(page + 1);
    pager.current?.scrollTo({ x: (page + 1) * width, animated: true });
  };
  const onScroll = (e: NativeSyntheticEvent<NativeScrollEvent>) => {
    const at = Math.min(LAST, Math.max(0, Math.round(e.nativeEvent.contentOffset.x / width)));
    if (at !== page) setPage(at);
  };

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
        onMomentumScrollEnd={onScroll}
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
            <View key={i} testID={i === page ? "intro-dot-on" : "intro-dot"} style={[styles.dot, i === page ? { width: 24, backgroundColor: colors.brandPrimary } : { backgroundColor: colors.borderStrong }]} />
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
