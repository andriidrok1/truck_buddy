// Web fallback map (react-native-maps does not run on web). Renders OpenStreetMap
// raster tiles with a tiny mercator projection, drag-to-pan,
// pins, route line and the driver arrow. Native uses MapLayer.native.tsx.
import { Image } from "expo-image";
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { PanResponder, Pressable, StyleSheet, View, type LayoutChangeEvent } from "react-native";

import { useTheme } from "../theme";
import type { LatLng } from "../types";
import type { MapLayerHandle, MapLayerProps } from "./mapTypes";
import { DestinationLabel, ENTRY_SIZE, EntryMarker, Pin, PIN_H, PIN_W, USER_SIZE, UserArrow } from "./Pin";
import { entrySpot } from "../utils/entry";

const TILE = 256;
const DEFAULT_ZOOM = 15;
const FOLLOW_ZOOM = 17;

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

const MapLayer = forwardRef<MapLayerHandle, MapLayerProps>(function MapLayer(props, ref) {
  const { driver, heading, places, active, topInset, bottomInset, fitKey, fitPoints, onCenterChange, onPlacePress, follow, onUserPan, onMapTouch } = props;
  const panCb = useRef(onUserPan);
  panCb.current = follow ? onUserPan : undefined;
  const touchCb = useRef(onMapTouch);
  touchCb.current = onMapTouch;
  const { colors } = useTheme();
  const [size, setSize] = useState({ w: 390, h: 844 });
  const [view, setView] = useState({ center: driver, zoom: DEFAULT_ZOOM });
  const viewRef = useRef(view);
  viewRef.current = view;

  // Driving mode: zoom in on the truck and keep following it.
  useEffect(() => {
    if (follow) setView({ center: driver, zoom: FOLLOW_ZOOM });
  }, [follow, driver]);

  // Focus point = middle of the area not covered by the overlays.
  const fx = size.w / 2;
  const fy = topInset + Math.max(80, size.h - topInset - bottomInset) / 2;

  const recenter = () => setView({ center: driver, zoom: DEFAULT_ZOOM });
  const zoomBy = (delta: number) =>
    setView((v) => ({ center: v.center, zoom: Math.min(18, Math.max(3, v.zoom + delta)) }));
  useImperativeHandle(ref, () => ({ recenter, zoomBy }));

  useEffect(() => {
    if (fitPoints.length === 0) return;
    if (fitPoints.length === 1) {
      setView({ center: fitPoints[0], zoom: DEFAULT_ZOOM });
      return;
    }
    const availW = size.w - 112;
    const availH = Math.max(120, size.h - topInset - bottomInset - 110);
    for (let z = 16; z >= 3; z--) {
      const pts = fitPoints.map((p) => project(p, z));
      const xs = pts.map((p) => p.x);
      const ys = pts.map((p) => p.y);
      const w = Math.max(...xs) - Math.min(...xs);
      const h = Math.max(...ys) - Math.min(...ys);
      if (w <= availW && h <= availH) {
        const c = unproject((Math.max(...xs) + Math.min(...xs)) / 2, (Math.max(...ys) + Math.min(...ys)) / 2 - 10, z);
        setView({ center: c, zoom: z });
        return;
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitKey]);

  const pan = useMemo(() => {
    let start = { x: 0, y: 0 };
    let pinch: { dist: number; zoom: number } | null = null;
    const touchDist = (t: readonly { pageX: number; pageY: number }[]) =>
      Math.hypot(t[0].pageX - t[1].pageX, t[0].pageY - t[1].pageY);
    return PanResponder.create({
      onMoveShouldSetPanResponder: (e, g) => e.nativeEvent.touches.length === 2 || Math.abs(g.dx) + Math.abs(g.dy) > 4,
      onPanResponderGrant: () => {
        touchCb.current?.();
        panCb.current?.();
        start = project(viewRef.current.center, viewRef.current.zoom);
        pinch = null;
      },
      onPanResponderMove: (e, g) => {
        const touches = e.nativeEvent.touches;
        if (touches.length === 2) {
          // Pinch: zoom around the current center (tiles need whole zoom levels).
          const d = touchDist(touches);
          if (!pinch) pinch = { dist: d, zoom: viewRef.current.zoom };
          const z = Math.min(18, Math.max(3, Math.round(pinch.zoom + Math.log2(d / pinch.dist))));
          if (z !== viewRef.current.zoom) setView({ zoom: z, center: viewRef.current.center });
          return;
        }
        if (pinch) return; // ignore the leftover finger after a pinch
        const z = viewRef.current.zoom;
        setView({ zoom: z, center: unproject(start.x - g.dx, start.y - g.dy, z) });
      },
      onPanResponderRelease: () => onCenterChange(viewRef.current.center),
    });
  }, [onCenterChange]);

  const z = view.zoom;
  const c = project(view.center, z);
  const left = c.x - fx;
  const top = c.y - fy;
  const toScreen = (p: LatLng) => {
    const q = project(p, z);
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
          source={{ uri: `https://tile.openstreetmap.org/${z}/${wx}/${ty}.png` }}
          style={{ position: "absolute", left: tx * TILE - left, top: ty * TILE - top, width: TILE, height: TILE }}
          transition={0}
          cachePolicy="memory-disk"
        />,
      );
    }
  }

  const segments = (line: [number, number][], width: number, color: string, prefix: string) => {
    const pts = line.map(([lat, lng]) => toScreen({ lat, lng }));
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

  const me = toScreen(driver);
  const dest = active ? toScreen(active.place) : null;
  const e = entrySpot(active?.parking);
  const entry = e ? toScreen(e) : null;

  return (
    <View
      testID="map-view"
      style={[StyleSheet.absoluteFill, styles.root, { backgroundColor: colors.mapPlaceholder }]}
      onLayout={(e: LayoutChangeEvent) => setSize({ w: e.nativeEvent.layout.width, h: e.nativeEvent.layout.height })}
      {...pan.panHandlers}
    >
      {tiles}
      {active && segments(active.route.geometry, 10, colors.routeCasing, "case")}
      {active && segments(active.route.geometry, 6, colors.routeLine, "line")}
      {active &&
        (active.route.traffic ?? []).map((t, i) =>
          segments(t.points, 6, t.level === "jam" ? colors.trafficJam : colors.trafficSlow, `traffic-${i}`),
        )}

      {places
        .filter((p) => p.id !== active?.place.id)
        .map((p) => {
          const s = toScreen(p);
          return (
            <Pressable
              key={p.id}
              testID={`map-pin-${p.id}`}
              accessibilityLabel={p.name}
              onPress={() => onPlacePress(p)}
              style={{ position: "absolute", left: s.x - PIN_W / 2, top: s.y - PIN_H }}
            >
              <Pin category={p.category} />
            </Pressable>
          );
        })}

      {active && dest && (
        <>
          <View style={{ position: "absolute", left: dest.x, top: dest.y - 50 }} pointerEvents="none">
            <DestinationLabel name={active.place.name.split(",")[0]} />
          </View>
          <View testID="map-destination-pin" style={{ position: "absolute", left: dest.x - PIN_W / 2, top: dest.y - PIN_H }} pointerEvents="none">
            <Pin category={active.place.category} selected />
          </View>
        </>
      )}

      {active && entry && (
        <View style={{ position: "absolute", left: entry.x - ENTRY_SIZE / 2, top: entry.y - ENTRY_SIZE / 2 }} pointerEvents="none">
          <EntryMarker />
        </View>
      )}

      <View testID="map-user-arrow" style={{ position: "absolute", left: me.x - USER_SIZE / 2, top: me.y - USER_SIZE / 2 }} pointerEvents="none">
        <UserArrow heading={heading} />
      </View>
    </View>
  );
});

const styles = StyleSheet.create({
  root: { overflow: "hidden" },
});

export default MapLayer;
