import { useEffect, useRef, useState } from "react";
import { PanResponder, StyleSheet, View, type LayoutChangeEvent } from "react-native";

import { t } from "../i18n";
import { Text } from "../i18n/Text";
import { frameClock } from "../map/mapView";
import { fonts, shadow, spacing, useTheme } from "../theme";
import { chipValue, detail, KIND_ICON, type Chip } from "../weather/routeWeather";
import type { TrackPart } from "../weather/timeSlider";
import { Icon } from "./Icon";
import { PressableScale } from "./PressableScale";
import { KIND_COLOR } from "./WeatherChip";

export const STEP_MIN = 15;
const PLAY_TICK_MS = 600;
const THUMB = 40;

type Props = {
  /** Minutes from now to arrival: the track's length. */
  totalMin: number;
  /** The slider's moment, minutes from now. */
  value: number;
  onChange: (min: number) => void;
  parts: TrackPart[];
  /** The forecast point at the slider's moment (header); null before the forecast came in. */
  chip: Chip | null;
  now: Date;
};

/** "Weather at a moment" (Figma V4): only shown parked or stopped (FMCSA: no dragging while driving). The
 *  track is colored by what the route meets, the warning stretch outlined, the far hours paler. */
export function TimeSlider({ totalMin, value, onChange, parts, chip, now }: Props) {
  const { colors, scheme } = useTheme();
  const [width, setWidth] = useState(0);
  const [playing, setPlaying] = useState(false);
  const live = useRef({ width, totalMin, onChange, value });
  live.current = { width, totalMin, onChange, value };

  const toMin = (x: number) => {
    const { width: w, totalMin: total } = live.current;
    if (w <= 0) return 0;
    const raw = (Math.max(0, Math.min(w, x)) / w) * total;
    return Math.min(total, Math.round(raw / STEP_MIN) * STEP_MIN);
  };
  const pan = useRef(
    PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onMoveShouldSetPanResponder: () => true,
      onPanResponderTerminationRequest: () => false,
      onPanResponderGrant: (e) => {
        setPlaying(false);
        live.current.onChange(toMin(e.nativeEvent.locationX));
      },
      onPanResponderMove: (e) => live.current.onChange(toMin(e.nativeEvent.locationX)),
    }),
  ).current;

  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => {
      const { value: v, totalMin: total, onChange: set } = live.current;
      if (v >= total) {
        setPlaying(false);
        return;
      }
      set(Math.min(total, v + STEP_MIN));
    }, PLAY_TICK_MS);
    return () => clearInterval(id);
  }, [playing]);

  const at = new Date(now.getTime() + value * 60000);
  const x = (m: number) => (totalMin > 0 ? (m / totalMin) * width : 0);
  const hrs = Math.floor(value / 60);
  const mins = value % 60;
  const small = value === 0 ? t("Weather now") : t("Weather in {h} h {m} min", { h: hrs, m: mins });
  const tint = chip ? (KIND_COLOR[chip.kind] ?? KIND_COLOR.cloud)[scheme === "dark" ? "dark" : "light"] : colors.muted;
  const warnStart = parts.find((p) => p.warn);
  const warnEnd = [...parts].reverse().find((p) => p.warn);

  return (
    <View testID="time-slider" style={styles.wrap}>
      <View style={styles.head}>
        <View style={styles.headLeft}>
          <Text style={[styles.small, { color: colors.onSurfaceSecondary }]}>{small}</Text>
          <Text testID="time-slider-clock" style={[styles.big, { color: colors.onSurface }]}>{frameClock(at.toISOString()) ?? ""}</Text>
        </View>
        {chip && (
          <View style={styles.headRight}>
            <Text numberOfLines={1} style={[styles.small, { color: colors.onSurfaceSecondary }]}>{chip.place?.split(",")[0] ?? ""}</Text>
            <View style={styles.cond}>
              <Text numberOfLines={1} style={[styles.condText, { color: chip.warn ? colors.warning : colors.onSurfaceSecondary }]}>{detail(chip).split(" · ")[0]}</Text>
              <Icon name={KIND_ICON[chip.kind] ?? "weather-cloudy"} size={26} color={tint} />
              <Text style={[styles.big, { color: colors.onSurface }]}>{chipValue(chip)}</Text>
            </View>
          </View>
        )}
      </View>
      <View style={styles.row}>
        <PressableScale
          testID="time-slider-play"
          accessibilityRole="button"
          accessibilityLabel={playing ? t("Pause") : t("Play")}
          onPress={() => {
            if (!playing && value >= totalMin) onChange(0);
            setPlaying((p) => !p);
          }}
          style={[styles.play, { backgroundColor: colors.surfaceSecondary }]}
        >
          <Icon name={playing ? "pause" : "play"} size={24} color={colors.onSurface} />
        </PressableScale>
        <View
          testID="time-slider-track"
          style={styles.trackArea}
          onLayout={(e: LayoutChangeEvent) => setWidth(e.nativeEvent.layout.width)}
          accessible
          accessibilityRole="adjustable"
          accessibilityLabel={t("Weather time")}
          accessibilityValue={{ text: frameClock(at.toISOString()) ?? "" }}
          accessibilityActions={[{ name: "increment" }, { name: "decrement" }]}
          onAccessibilityAction={(e) => onChange(Math.max(0, Math.min(totalMin, value + (e.nativeEvent.actionName === "increment" ? STEP_MIN : -STEP_MIN))))}
          {...pan.panHandlers}
        >
          <View pointerEvents="none" style={[styles.track, { backgroundColor: colors.surfaceTertiary }]}>
            {parts.map((p, i) => (
              <View key={i} style={[styles.part, { left: x(p.from), width: Math.max(0, x(p.to) - x(p.from) - 1), backgroundColor: p.color, opacity: p.rough ? 0.45 : 1 }]} />
            ))}
          </View>
          {warnStart && warnEnd && (
            <View pointerEvents="none" style={[styles.warn, { left: x(warnStart.from) - 2, width: x(warnEnd.to) - x(warnStart.from) + 4, borderColor: colors.warning }]} />
          )}
          <View pointerEvents="none" style={[styles.thumb, shadow(colors), { left: x(value) - THUMB / 2, backgroundColor: colors.surface, borderColor: colors.info }]} />
        </View>
      </View>
      <View style={styles.ticks}>
        <Text style={[styles.tick, { color: colors.onSurfaceSecondary }]}>Now</Text>
        <Text style={[styles.tick, { color: colors.onSurfaceSecondary }]}>{frameClock(new Date(now.getTime() + totalMin * 60000).toISOString()) ?? ""}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { marginTop: spacing.md, gap: spacing.sm },
  head: { flexDirection: "row", alignItems: "flex-end", justifyContent: "space-between", gap: spacing.md },
  headLeft: { flexShrink: 0 },
  headRight: { flex: 1, alignItems: "flex-end" },
  small: { fontFamily: fonts.semibold, fontSize: 14 },
  big: { fontFamily: fonts.extrabold, fontSize: 24 },
  cond: { flexDirection: "row", alignItems: "center", gap: 6 },
  condText: { fontFamily: fonts.bold, fontSize: 15, flexShrink: 1 },
  row: { flexDirection: "row", alignItems: "center", gap: spacing.md },
  play: { width: 52, height: 52, borderRadius: 26, alignItems: "center", justifyContent: "center" },
  trackArea: { flex: 1, height: 52, justifyContent: "center" },
  track: { height: 10, borderRadius: 5, overflow: "hidden" },
  part: { position: "absolute", top: 0, bottom: 0 },
  warn: { position: "absolute", height: 20, borderRadius: 10, borderWidth: 2, borderStyle: "dashed" },
  thumb: { position: "absolute", width: THUMB, height: THUMB, borderRadius: THUMB / 2, borderWidth: 3 },
  ticks: { flexDirection: "row", justifyContent: "space-between", paddingLeft: 52 + spacing.md },
  tick: { fontFamily: fonts.semibold, fontSize: 13 },
});
