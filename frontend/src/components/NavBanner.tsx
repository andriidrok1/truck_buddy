import { StyleSheet, Text, View } from "react-native";
import Animated, { FadeInRight, FadeInUp, ZoomIn } from "react-native-reanimated";

import type { Advisory } from "../geo/advisories";
import { formatDistance } from "../hooks/useGuidance";
import { fonts, radius, shadow, spacing, useTheme } from "../theme";
import type { Maneuver } from "../types";
import { AdvisoryStrip } from "./AdvisoryStrip";
import { Icon } from "./Icon";
import { PressableScale } from "./PressableScale";

function maneuverIcon(m: Maneuver | null) {
  if (!m) return "arrow-up";
  const left = m.direction === "left";
  const right = m.direction === "right";
  switch (m.action) {
    case "arrive":
      return "flag-checkered";
    case "uTurn":
      return "arrow-u-left-top";
    case "keep":
    case "ramp":
    case "exit":
    case "continueHighway":
    case "enterHighway":
      return left ? "arrow-top-left" : right ? "arrow-top-right" : "arrow-up";
    case "roundaboutEnter":
    case "roundaboutPass":
    case "roundaboutExit":
      return "rotate-right";
    default:
      return left ? "arrow-left-top" : right ? "arrow-right-top" : "arrow-up";
  }
}

type Props = {
  topInset: number;
  next: Maneuver | null;
  toNextM: number;
  voiceOn: boolean;
  onToggleVoice: () => void;
  onLayout: (h: number) => void;
  /** Posted limit here (US shield), null = unknown: no shield. */
  limitMph?: number | null;
  /** Current speed, mph; shown under the shield, red when over the limit. */
  speedMph?: number | null;
  /** What is ahead: chips under the banner. */
  advisories?: Advisory[];
};

/** US speed-limit sign: white, black border, "SPEED LIMIT" over the number. */
function SpeedShield({ limitMph, speedMph }: { limitMph: number; speedMph: number | null }) {
  const { colors } = useTheme();
  const over = speedMph != null && speedMph > limitMph;
  return (
    <View style={styles.shieldWrap} testID="speed-shield">
      <View style={styles.shield}>
        <Text style={styles.shieldLabel}>SPEED</Text>
        <Text style={styles.shieldLabel}>LIMIT</Text>
        <Text testID="speed-limit" style={styles.shieldNumber}>
          {limitMph}
        </Text>
      </View>
      {speedMph != null && (
        <Text testID="speed-now" style={[styles.speedNow, { color: over ? colors.error : colors.onSurfaceInverse }]}>
          {speedMph}
        </Text>
      )}
    </View>
  );
}

/** Driving mode top banner: next maneuver, distance to it, posted limit and the spoken-directions toggle. */
export function NavBanner({ topInset, next, toNextM, voiceOn, onToggleVoice, onLayout, limitMph = null, speedMph = null, advisories = [] }: Props) {
  const { colors } = useTheme();
  return (
    <View
      style={[styles.wrap, { paddingTop: topInset + spacing.sm }]}
      pointerEvents="box-none"
      onLayout={(e) => onLayout(e.nativeEvent.layout.height)}
    >
      <Animated.View entering={FadeInUp.springify().damping(16)} testID="nav-banner" style={[styles.card, shadow(colors, 2), { backgroundColor: colors.surfaceInverse }]}>
        <Animated.View key={`ic-${next?.instruction ?? "-"}`} entering={ZoomIn.springify().damping(14)} style={[styles.arrow, { backgroundColor: colors.info }]}>
          <Icon name={maneuverIcon(next)} size={36} color={colors.onInfo} />
        </Animated.View>
        <Animated.View key={`tx-${next?.instruction ?? "-"}`} entering={FadeInRight.duration(260)} style={styles.text}>
          <Text testID="nav-distance" style={[styles.dist, { color: colors.onSurfaceInverse }]}>
            {formatDistance(toNextM)}
          </Text>
          <Text testID="nav-instruction" numberOfLines={2} style={[styles.instr, { color: colors.onSurfaceInverse }]}>
            {next?.instruction ?? "Follow the route"}
          </Text>
        </Animated.View>
        {limitMph != null && <SpeedShield limitMph={limitMph} speedMph={speedMph} />}
        <PressableScale
          testID="voice-guidance-toggle"
          accessibilityLabel={voiceOn ? "Mute directions" : "Unmute directions"}
          onPress={onToggleVoice}
          style={[styles.voice, { backgroundColor: voiceOn ? colors.surface : colors.error }]}
        >
          <Icon name={voiceOn ? "volume-high" : "volume-off"} size={24} color={voiceOn ? colors.onSurface : colors.onError} />
        </PressableScale>
      </Animated.View>
      <AdvisoryStrip advisories={advisories} />
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { position: "absolute", top: 0, left: 0, right: 0, paddingHorizontal: spacing.lg },
  card: { flexDirection: "row", alignItems: "center", gap: spacing.md, padding: spacing.md, borderRadius: radius.lg },
  arrow: { width: 60, height: 60, borderRadius: 16, alignItems: "center", justifyContent: "center" },
  text: { flex: 1, minWidth: 0 },
  dist: { fontFamily: fonts.extrabold, fontSize: 26, letterSpacing: -0.3 },
  instr: { fontFamily: fonts.semibold, fontSize: 16 },
  voice: { width: 48, height: 48, borderRadius: 24, alignItems: "center", justifyContent: "center" },
  shieldWrap: { alignItems: "center", gap: 2 },
  shield: { width: 44, paddingVertical: 3, borderRadius: 6, borderWidth: 2, borderColor: "#000000", backgroundColor: "#FFFFFF", alignItems: "center" },
  shieldLabel: { fontFamily: fonts.bold, fontSize: 7, lineHeight: 8, color: "#000000", letterSpacing: 0.2 },
  shieldNumber: { fontFamily: fonts.extrabold, fontSize: 18, lineHeight: 20, color: "#000000", marginTop: 1 },
  speedNow: { fontFamily: fonts.bold, fontSize: 12, fontVariant: ["tabular-nums"] },
});
