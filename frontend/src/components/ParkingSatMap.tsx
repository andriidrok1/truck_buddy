// Web version (react-native-maps does not run on web): Esri imagery tiles with
// drag-to-pan, pinch / +- zoom, and the same E / P / place / route-tail overlays.
import { Image } from "expo-image";
import { useEffect, useRef, useState } from "react";
import { PanResponder, StyleSheet, View, type LayoutChangeEvent } from "react-native";

import { radius, useTheme } from "../theme";
import type { LatLng } from "../types";
import { Icon } from "./Icon";
import { ESRI_TILES, type ParkingSatMapProps } from "./mapTypes";
import { Pin, PIN_H, PIN_W } from "./Pin";
import { PressableScale } from "./PressableScale";
import { SpotBadge } from "./SpotBadge";

const TILE = 256;
const MAX_Z = 19;
const BADGE = 30;

function project(p: LatLng, z: number) {
  const s = TILE * 2 ** z;
  const sin = Math.sin((p.lat * Math.PI) / 180);
  return { x: ((p.lng + 180) / 360) * s, y: (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * s };
}

function unproject(x: number, y: number, z: number): LatLng {
  const s = TILE * 2 ** z;
  const n = Math.PI - (2 * Math.PI * y) / s;
  return { lat: (180 / Math.PI) * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n))), lng: (x / s) * 360 - 180 };
}

/** Whole zoom level at which ~300 m fits the view width. */
function frameZoom(lat: number, width: number) {
  const mPerPxZ0 = 156543.03 * Math.cos((lat * Math.PI) / 180);
  return Math.min(MAX_Z, Math.max(3, Math.floor(Math.log2((mPerPxZ0 * width) / 300))));
}

export default function ParkingSatMap({ center, place, spots, tail }: ParkingSatMapProps) {
  const { colors } = useTheme();
  const [size, setSize] = useState({ w: 390, h: 600 });
  const [view, setView] = useState({ center, zoom: frameZoom(center.lat, 390) });
  const viewRef = useRef(view);
  useEffect(() => {
    viewRef.current = view;
  }, [view]);
  const sized = useRef(false);

  const onLayout = (e: LayoutChangeEvent) => {
    const { width, height } = e.nativeEvent.layout;
    setSize({ w: width, h: height });
    if (!sized.current) {
      sized.current = true;
      setView({ center, zoom: frameZoom(center.lat, Math.min(width, height)) });
    }
  };

  const zoomBy = (d: number) => setView((v) => ({ center: v.center, zoom: Math.min(MAX_Z, Math.max(3, v.zoom + d)) }));

  const [pan] = useState(() => {
    let start = { x: 0, y: 0 };
    let pinch: { dist: number; zoom: number } | null = null;
    const dist = (t: readonly { pageX: number; pageY: number }[]) => Math.hypot(t[0].pageX - t[1].pageX, t[0].pageY - t[1].pageY);
    return PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onMoveShouldSetPanResponder: () => true,
      onPanResponderGrant: () => {
        start = project(viewRef.current.center, viewRef.current.zoom);
        pinch = null;
      },
      onPanResponderMove: (e, g) => {
        const t = e.nativeEvent.touches;
        if (t.length === 2) {
          const d = dist(t);
          if (!pinch) pinch = { dist: d, zoom: viewRef.current.zoom };
          const z = Math.min(MAX_Z, Math.max(3, Math.round(pinch.zoom + Math.log2(d / pinch.dist))));
          if (z !== viewRef.current.zoom) setView({ zoom: z, center: viewRef.current.center });
          return;
        }
        if (pinch) return;
        const z = viewRef.current.zoom;
        setView({ zoom: z, center: unproject(start.x - g.dx, start.y - g.dy, z) });
      },
    });
  });

  const z = view.zoom;
  const c = project(view.center, z);
  const left = c.x - size.w / 2;
  const top = c.y - size.h / 2;
  const toScreen = (lat: number, lng: number) => {
    const q = project({ lat, lng }, z);
    return { x: q.x - left, y: q.y - top };
  };

  const tiles = [];
  const n = 2 ** z;
  for (let tx = Math.floor(left / TILE); tx <= Math.floor((left + size.w) / TILE); tx++) {
    for (let ty = Math.floor(top / TILE); ty <= Math.floor((top + size.h) / TILE); ty++) {
      if (ty < 0 || ty >= n) continue;
      const wx = ((tx % n) + n) % n;
      tiles.push(
        <Image
          key={`${z}-${tx}-${ty}`}
          source={{ uri: ESRI_TILES.replace("{z}", String(z)).replace("{y}", String(ty)).replace("{x}", String(wx)) }}
          style={{ position: "absolute", left: tx * TILE - left, top: ty * TILE - top, width: TILE, height: TILE }}
          transition={0}
          cachePolicy="memory-disk"
        />,
      );
    }
  }

  const segments = (width: number, color: string, prefix: string) => {
    const pts = tail.map(([a, b]) => toScreen(a, b));
    return pts.slice(1).map((b, i) => {
      const a = pts[i];
      const len = Math.hypot(b.x - a.x, b.y - a.y) + width / 2;
      const ang = Math.atan2(b.y - a.y, b.x - a.x);
      return (
        <View
          key={`${prefix}-${i}`}
          pointerEvents="none"
          style={{
            position: "absolute",
            left: (a.x + b.x) / 2 - len / 2,
            top: (a.y + b.y) / 2 - width / 2,
            width: len,
            height: width,
            borderRadius: width / 2,
            backgroundColor: color,
            transform: [{ rotate: `${ang}rad` }],
          }}
        />
      );
    });
  };

  const pin = place ? toScreen(place.lat, place.lng) : null;

  return (
    <View testID="parking-sat-map" style={[StyleSheet.absoluteFill, styles.root, { backgroundColor: colors.surfaceInverse }]} onLayout={onLayout}>
      <View style={StyleSheet.absoluteFill} {...pan.panHandlers}>
        {/* Not hit-testable, so the browser can't start an image drag and steal the pan */}
        <View pointerEvents="none" style={StyleSheet.absoluteFill}>
          {tiles}
        </View>
        {tail.length > 1 && segments(9, colors.routeCasing, "case")}
        {tail.length > 1 && segments(5, colors.routeLine, "line")}
        {pin && place && (
          <View testID="parking-sat-place-pin" pointerEvents="none" style={{ position: "absolute", left: pin.x - PIN_W / 2, top: pin.y - PIN_H }}>
            <Pin category={place.category} selected />
          </View>
        )}
        {spots.map((s, i) => {
          const p = toScreen(s.lat, s.lng);
          return (
            <View
              key={`${s.kind}-${i}`}
              testID={`parking-sat-spot-${s.kind}-${i}`}
              pointerEvents="none"
              style={{ position: "absolute", left: p.x - BADGE / 2, top: p.y - BADGE / 2 }}
            >
              <SpotBadge kind={s.kind} size={BADGE} />
            </View>
          );
        })}
      </View>
      <View style={styles.zoomCol}>
        {[
          { d: 1, icon: "plus", id: "in" },
          { d: -1, icon: "minus", id: "out" },
        ].map((b) => (
          <PressableScale
            key={b.id}
            testID={`parking-sat-zoom-${b.id}`}
            accessibilityLabel={`Zoom ${b.id}`}
            onPress={() => zoomBy(b.d)}
            style={[styles.zoomBtn, { backgroundColor: colors.surface }]}
          >
            <Icon name={b.icon} size={22} color={colors.onSurface} />
          </PressableScale>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { overflow: "hidden" },
  zoomCol: { position: "absolute", right: 16, bottom: 16, gap: 8 },
  zoomBtn: { width: 44, height: 44, borderRadius: radius.md, alignItems: "center", justifyContent: "center" },
});
