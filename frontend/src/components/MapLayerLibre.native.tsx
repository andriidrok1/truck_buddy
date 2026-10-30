// MapLibre version of the map layer (dev builds with EXPO_PUBLIC_MAP_PROVIDER=maplibre only).
// OpenFreeMap vector tiles, the same route line, traffic colours, pins and camera behaviour as
// MapLayerApple. The library is loaded lazily (src/map/libre.ts) so this file is safe to import
// in Expo Go: it renders nothing there, and the provider switch never picks it anyway.
// Offline: once a corridor pack for the route exists (src/map/offline.ts) MapLibre serves the
// tiles from it by itself; nothing here knows about packs.

import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef } from "react";
import { StyleSheet, Text, View } from "react-native";

import { loadMapLibre } from "../map/libre";
import { OPENFREEMAP_ATTRIBUTION, OPENFREEMAP_DARK_STYLE, OPENFREEMAP_STYLE } from "../map/provider";
import { routeBounds, toLngLatBounds } from "../map/bounds";
import { fonts, useTheme } from "../theme";
import type { LatLng } from "../types";
import { entrySpot } from "../utils/entry";
import type { MapLayerHandle, MapLayerProps } from "./mapTypes";
import { DestinationLabel, EntryMarker, Pin, UserArrow } from "./Pin";

const lib = loadMapLibre();

type LngLat = [number, number];
type CameraRef = import("@maplibre/maplibre-react-native").CameraRef;
type MapRef = import("@maplibre/maplibre-react-native").MapRef;

const toLngLat = (p: LatLng): LngLat => [p.lng, p.lat];
const lineFeature = (points: readonly (readonly [number, number])[], properties: Record<string, unknown> = {}): GeoJSON.Feature<GeoJSON.LineString> => ({
  type: "Feature",
  properties,
  geometry: { type: "LineString", coordinates: points.map(([lat, lng]) => [lng, lat]) },
});

const FOLLOW_ZOOM = 17;
const DEFAULT_ZOOM = 15;
const FIT_PADDING = { top: 48, right: 56, bottom: 48, left: 56 };

const MapLayerLibre = forwardRef<MapLayerHandle, MapLayerProps>(function MapLayerLibre(props, ref) {
  const { driver, heading, places, active, topInset, bottomInset, fitKey, fitPoints, onCenterChange, onPlacePress, follow, onUserPan, onMapTouch } = props;
  const { colors, scheme } = useTheme();
  const camera = useRef<CameraRef>(null);
  const map = useRef<MapRef>(null);

  // Driving mode: close, tilted camera that follows the truck and turns with it.
  useEffect(() => {
    if (!follow) return;
    camera.current?.easeTo({ center: toLngLat(driver), zoom: FOLLOW_ZOOM, pitch: 45, bearing: heading ?? 0, duration: 600 });
  }, [follow, driver, heading]);
  useEffect(() => {
    if (!follow) camera.current?.setStop({ pitch: 0, bearing: 0, duration: 400 });
  }, [follow]);

  const recenter = () => camera.current?.easeTo({ center: toLngLat(driver), zoom: DEFAULT_ZOOM, duration: 450 });
  const zoomBy = async (delta: number) => {
    const z = await map.current?.getZoom();
    if (z != null) camera.current?.zoomTo(Math.min(20, Math.max(3, z + delta)), { duration: 350 });
  };
  useImperativeHandle(ref, () => ({ recenter, zoomBy }));

  useEffect(() => {
    if (fitPoints.length > 1) {
      const b = routeBounds(fitPoints.map((p) => [p.lat, p.lng] as [number, number]), 0);
      if (b) camera.current?.fitBounds(toLngLatBounds(b), { padding: FIT_PADDING, duration: 500 });
    } else if (fitPoints.length === 1) {
      camera.current?.easeTo({ center: toLngLat(fitPoints[0]), zoom: DEFAULT_ZOOM, duration: 450 });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitKey]);

  const route = active?.route;
  const routeData = useMemo(() => (route ? lineFeature(route.geometry) : null), [route]);
  const trafficData = useMemo<GeoJSON.FeatureCollection | null>(
    () => (route?.traffic?.length ? { type: "FeatureCollection", features: route.traffic.map((t) => lineFeature(t.points, { level: t.level })) } : null),
    [route],
  );

  if (!lib) return null;
  const { Map, Camera, GeoJSONSource, Layer, Marker } = lib;
  const entry = entrySpot(active?.parking);

  return (
    <View style={StyleSheet.absoluteFill} testID="map-view-libre">
      <Map
        ref={map}
        style={StyleSheet.absoluteFill}
        mapStyle={scheme === "dark" ? OPENFREEMAP_DARK_STYLE : OPENFREEMAP_STYLE}
        contentInset={{ top: topInset, bottom: bottomInset }}
        attribution
        attributionPosition={{ bottom: bottomInset + 8, right: 8 }}
        logo={false}
        compass={false}
        onPress={onMapTouch}
        onRegionIsChanging={(e) => {
          if (!e.nativeEvent.userInteraction) return;
          onMapTouch?.();
          if (follow) onUserPan?.();
        }}
        onRegionDidChange={(e) => onCenterChange({ lat: e.nativeEvent.center[1], lng: e.nativeEvent.center[0] })}
      >
        <Camera ref={camera} initialViewState={{ center: toLngLat(driver), zoom: DEFAULT_ZOOM }} />

        {routeData && (
          <GeoJSONSource id="route" data={routeData}>
            <Layer id="route-casing" type="line" source="route" layout={{ "line-cap": "round", "line-join": "round" }} paint={{ "line-color": colors.routeCasing, "line-width": 10 }} />
            <Layer id="route-line" type="line" source="route" layout={{ "line-cap": "round", "line-join": "round" }} paint={{ "line-color": colors.routeLine, "line-width": 6 }} />
          </GeoJSONSource>
        )}
        {trafficData && (
          <GeoJSONSource id="traffic" data={trafficData}>
            <Layer
              id="traffic-line"
              type="line"
              source="traffic"
              layout={{ "line-cap": "round", "line-join": "round" }}
              paint={{ "line-color": ["match", ["get", "level"], "jam", colors.trafficJam, colors.trafficSlow], "line-width": 6 }}
            />
          </GeoJSONSource>
        )}

        {places
          .filter((p) => p.id !== active?.place.id)
          .map((p) => (
            <Marker key={p.id} id={`place-${p.id}`} lngLat={toLngLat(p)} anchor="bottom" onPress={() => onPlacePress(p)}>
              <Pin category={p.category} />
            </Marker>
          ))}

        {active && (
          <>
            <Marker key={`dest-${active.place.id}`} id={`dest-${active.place.id}`} lngLat={toLngLat(active.place)} anchor="bottom">
              <Pin category={active.place.category} selected />
            </Marker>
            <Marker key={`label-${active.place.id}`} id={`label-${active.place.id}`} lngLat={toLngLat(active.place)} anchor="bottom-left">
              <DestinationLabel name={active.place.name} />
            </Marker>
            {entry && (
              <Marker key={`entry-${active.place.id}`} id={`entry-${active.place.id}`} lngLat={toLngLat(entry)} anchor="center">
                <EntryMarker />
              </Marker>
            )}
          </>
        )}

        <Marker key="driver" id="driver" lngLat={toLngLat(driver)} anchor="center">
          <UserArrow heading={follow ? 0 : heading} />
        </Marker>
      </Map>
      {/* OpenFreeMap asks for this line on screen; the native "i" button shows the full notice. */}
      <Text testID="map-attribution" style={[styles.attribution, { color: colors.onSurfaceSecondary, bottom: bottomInset + 4 }]} numberOfLines={1}>
        {OPENFREEMAP_ATTRIBUTION}
      </Text>
    </View>
  );
});

export default MapLayerLibre;

const styles = StyleSheet.create({
  attribution: { position: "absolute", left: 8, fontFamily: fonts.regular, fontSize: 10, opacity: 0.8 },
});
