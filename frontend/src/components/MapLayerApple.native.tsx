import { forwardRef, Fragment, useEffect, useImperativeHandle, useRef, useState, type ReactNode } from "react";
import { Platform, StyleSheet, useWindowDimensions } from "react-native";
import MapView, { Marker, Polyline, UrlTile } from "react-native-maps";

import { useTheme } from "../theme";
import type { LatLng } from "../types";
import type { MapLayerHandle, MapLayerProps } from "./mapTypes";
import { DestinationLabel, EntryMarker, Pin, UserArrow } from "./Pin";
import { entrySpot } from "../utils/entry";
import { cameraSettled, fitCamera } from "../map/fitCamera";
import { NIGHT_MAP_STYLE } from "../map/nightStyle";
import { frameClock, nativeMapType, RADAR_OPACITY, WEATHER_MAP_STYLE } from "../map/mapView";
import { chipMode, zoomOf } from "../weather/routeWeather";
import { AlertMarkerView, ChainMarkerView, GhostTruckView, WeatherChipView } from "./WeatherChip";

const toCoord = (p: LatLng) => ({ latitude: p.lat, longitude: p.lng });
const FOLLOW_ZOOM = 17;
const FOLLOW_PITCH = 45;
const REFIT_WINDOW_MS = 1500; // a padding change this soon after a fit re-runs the fit
const FIT_CHECK_MS = 900; // the fit animation is over by then: read the camera back and correct a dropped one
const FIT_MARGIN = 56; // layout units kept free around the fitted points, inside the map window
const FIT_MAX_ZOOM = 16;
const SINGLE_POINT_ZOOM = 15.5; // one point: about the 0.015-degree region the recenter button shows
const FIT_RETRIES = 2; // a dropped fit animation is sent again at most this many times

/** Custom-view marker that stops tracking view changes once its icon has rendered.
 *  A new `redraw` value re-renders the icon (Android otherwise keeps the first snapshot). */
export function ViewMarker({ children, redraw, ...rest }: React.ComponentProps<typeof Marker> & { children: ReactNode; redraw?: string | number }) {
  const [tracks, setTracks] = useState(true);
  const [drawn, setDrawn] = useState(redraw);
  if (drawn !== redraw) {
    setDrawn(redraw);
    setTracks(true);
  }
  useEffect(() => {
    const t = setTimeout(() => setTracks(false), 800);
    return () => clearTimeout(t);
  }, [redraw]);
  return (
    <Marker tracksViewChanges={tracks} {...rest}>
      {children}
    </Marker>
  );
}

const MapLayer = forwardRef<MapLayerHandle, MapLayerProps>(function MapLayer(props, ref) {
  const { driver, heading, places, active, alternatives, topInset, bottomInset, fitKey, fitPoints, onCenterChange, onPlacePress, follow, onUserPan, onMapTouch, mapView = "map", radar, weatherChips, onWeatherChipPress } = props;
  const [zoom, setZoom] = useState(10);
  const weather = mapView === "weather";
  const { colors, scheme } = useTheme();
  const { width: winW, height: winH } = useWindowDimensions();
  const mapRef = useRef<MapView>(null);
  const padding = { top: topInset, bottom: bottomInset, left: 0, right: 0 };

  // Driving mode: close, tilted camera that follows the truck and turns with it.
  // The sheet changes height in the same frame (Go: route card -> nav bar), and on Android the map's
  // padding update swallows the camera animation; the camera stayed on the whole-route view until the
  // truck moved. So the padding is a dependency, and once the animation should be over the camera is
  // checked and sent again if it never got there.
  useEffect(() => {
    if (!follow) return;
    const cam = { center: toCoord(driver), zoom: FOLLOW_ZOOM, pitch: FOLLOW_PITCH, heading: heading ?? 0 };
    mapRef.current?.animateCamera(cam, { duration: 600 });
    const t = setTimeout(async () => {
      const c = await mapRef.current?.getCamera().catch(() => null);
      if (c?.zoom != null && (Math.abs(c.zoom - FOLLOW_ZOOM) > 0.5 || Math.abs((c.pitch ?? 0) - FOLLOW_PITCH) > 5)) {
        mapRef.current?.animateCamera(cam, { duration: 400 });
      }
    }, 900);
    return () => clearTimeout(t);
  }, [follow, driver, heading, topInset, bottomInset]);
  useEffect(() => {
    if (!follow) mapRef.current?.animateCamera({ pitch: 0, heading: 0 }, { duration: 400 });
  }, [follow]);

  const recenter = () =>
    mapRef.current?.animateToRegion({ ...toCoord(driver), latitudeDelta: 0.015, longitudeDelta: 0.015 }, 450);

  const zoomBy = async (delta: number) => {
    const cam = await mapRef.current?.getCamera();
    if (cam?.zoom != null) mapRef.current?.animateCamera({ zoom: Math.min(20, Math.max(3, cam.zoom + delta)) }, { duration: 350 });
    else if (cam?.altitude != null) mapRef.current?.animateCamera({ altitude: cam.altitude / 2 ** delta }, { duration: 350 });
  };

  const centerOn = (p: LatLng) => mapRef.current?.animateCamera({ center: toCoord(p) }, { duration: 300 });
  useImperativeHandle(ref, () => ({ recenter, zoomBy, centerOn }));

  const fitAt = useRef(0);
  const fitCheck = useRef<ReturnType<typeof setTimeout> | null>(null);
  // A camera command before the map has loaded is dropped by the native view. The first fit (the GPS fix, a
  // restored route) can come in that window once the fix is in before this mounts, so it waits for
  // onMapReady and runs then.
  const ready = useRef(false);
  const pendingFit = useRef(false);
  // One camera for the points, computed here for the map window (the screen minus the chrome the
  // insets describe). The map padding already centres the camera in that window.
  // Android drops the animation now and then (the sheet growing for the route card changes the
  // padding in the same frames) and the camera stays on the previous view, so once the animation
  // should be over the camera is read back and sent again if it never got there (QA 2026-10-31, Н7).
  const fit = () => {
    if (!ready.current) {
      pendingFit.current = true;
      return;
    }
    if (fitCheck.current) clearTimeout(fitCheck.current);
    if (fitPoints.length === 0) return;
    // One point (the GPS fix, a tester's spot) goes through the same camera + read-back path as a set of
    // points: right after onMapReady Android still drops the first animation, and a plain animateToRegion
    // had no second try, so the map stayed on the demo point (stand, 2026-11-01).
    const cam =
      fitPoints.length > 1
        ? fitCamera(fitPoints, winW, Math.max(120, winH - topInset - bottomInset), FIT_MARGIN, FIT_MAX_ZOOM)
        : { center: fitPoints[0], zoom: SINGLE_POINT_ZOOM };
    const target = { center: toCoord(cam.center), zoom: cam.zoom, pitch: 0, heading: 0 };
    mapRef.current?.animateCamera(target, { duration: 450 });
    let tries = 0;
    const check = async () => {
      const c = await mapRef.current?.getCamera().catch(() => null);
      const actual = c?.center ? { center: { lat: c.center.latitude, lng: c.center.longitude }, zoom: c.zoom } : null;
      if (cameraSettled(cam, actual, winW, winH) || tries >= FIT_RETRIES) return;
      tries += 1;
      mapRef.current?.animateCamera(target, { duration: 400 });
      fitCheck.current = setTimeout(check, FIT_CHECK_MS);
    };
    fitCheck.current = setTimeout(check, FIT_CHECK_MS);
  };
  useEffect(() => {
    fitAt.current = Date.now();
    fit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitKey]);
  useEffect(() => () => {
    if (fitCheck.current) clearTimeout(fitCheck.current);
  }, []);
  // The sheet grows for a new route card right after the fit, so the window the fit was computed for
  // is gone: a padding change right after a fit fits again.
  useEffect(() => {
    if (fitAt.current && Date.now() - fitAt.current < REFIT_WINDOW_MS) fit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topInset, bottomInset]);

  return (
    <MapView
      ref={mapRef}
      testID="map-view"
      style={StyleSheet.absoluteFill}
      initialRegion={{ ...toCoord(driver), latitudeDelta: 0.015, longitudeDelta: 0.015 }}
      onMapReady={() => {
        ready.current = true;
        if (pendingFit.current) {
          pendingFit.current = false;
          fitAt.current = Date.now();
          fit();
        }
      }}
      mapType={nativeMapType(mapView, Platform.OS)}
      // Google styles apply to the drawn map only: none on satellite; the weather view mutes it on Android
      // (iOS gets the same from mapType "mutedStandard").
      customMapStyle={mapView === "satellite" ? undefined : scheme === "dark" ? NIGHT_MAP_STYLE : mapView === "weather" ? WEATHER_MAP_STYLE : undefined}
      userInterfaceStyle={scheme}
      mapPadding={padding}
      showsUserLocation={false}
      showsCompass={false}
      showsMyLocationButton={false}
      toolbarEnabled={false}
      showsPointsOfInterests={false}
      onPress={onMapTouch}
      onPanDrag={() => {
        onMapTouch?.();
        if (follow) onUserPan?.();
      }}
      onRegionChangeComplete={(r) => {
        onCenterChange({ lat: r.latitude, lng: r.longitude });
        setZoom(zoomOf(r.longitudeDelta, winW));
      }}
    >
      {mapView === "weather" && radar && (
        // Remounted per frame (key): a new urlTemplate on the same tile layer kept showing the cached old frame.
        <UrlTile
          key={radar.key}
          urlTemplate={radar.urlTemplate}
          maximumNativeZ={radar.maxNativeZoom}
          tileSize={256}
          opacity={radar.opacity ?? (scheme === "dark" ? RADAR_OPACITY.dark : RADAR_OPACITY.light)}
          zIndex={0}
        />
      )}
      {(alternatives ?? []).map((g, i) => (
        <Fragment key={`alt-${i}`}>
          <Polyline coordinates={g.map(([lat, lng]) => ({ latitude: lat, longitude: lng }))} strokeColor={colors.routeCasing} strokeWidth={9} />
          <Polyline coordinates={g.map(([lat, lng]) => ({ latitude: lat, longitude: lng }))} strokeColor={colors.muted} strokeWidth={5} />
        </Fragment>
      ))}
      {active && (
        <>
          <Polyline coordinates={active.route.geometry.map(([lat, lng]) => ({ latitude: lat, longitude: lng }))} strokeColor={colors.routeCasing} strokeWidth={10} />
          <Polyline coordinates={active.route.geometry.map(([lat, lng]) => ({ latitude: lat, longitude: lng }))} strokeColor={colors.routeLine} strokeWidth={6} />
          {(active.route.traffic ?? []).map((t, i) => (
            <Polyline
              key={`traffic-${i}`}
              coordinates={t.points.map(([lat, lng]) => ({ latitude: lat, longitude: lng }))}
              strokeColor={t.level === "jam" ? colors.trafficJam : colors.trafficSlow}
              strokeWidth={6}
              zIndex={2}
            />
          ))}
        </>
      )}

      {places
        .filter((p) => p.id !== active?.place.id)
        .map((p) => (
          <ViewMarker key={p.id} coordinate={toCoord(p)} anchor={{ x: 0.5, y: 1 }} onPress={() => onPlacePress(p)}>
            <Pin category={p.category} />
          </ViewMarker>
        ))}

      {active && (
        <>
          <ViewMarker key={`dest-${active.place.id}`} coordinate={toCoord(active.place)} anchor={{ x: 0.5, y: 1 }} zIndex={5}>
            <Pin category={active.place.category} selected />
          </ViewMarker>
          <ViewMarker key={`label-${active.place.id}`} coordinate={toCoord(active.place)} anchor={{ x: 0, y: 1 }} zIndex={4}>
            <DestinationLabel name={active.place.name} />
          </ViewMarker>
          {entrySpot(active.parking) && (
            <ViewMarker key={`entry-${active.place.id}`} coordinate={toCoord(entrySpot(active.parking)!)} anchor={{ x: 0.5, y: 0.5 }} zIndex={6}>
              <EntryMarker />
            </ViewMarker>
          )}
        </>
      )}

      {weather && active && (
        <>
          {(active.route.weather ?? []).map((w) => {
            const at = active.route.geometry[w.index];
            return at ? (
              <ViewMarker key={`wx-alert-${w.id}`} coordinate={{ latitude: at[0], longitude: at[1] }} anchor={{ x: 0.5, y: 0.5 }} zIndex={7}>
                <AlertMarkerView />
              </ViewMarker>
            ) : null;
          })}
          {(active.route.chain_controls ?? [])
            .filter((c) => c.index >= 0 && c.status !== "R-0")
            .map((c) => (
              <ViewMarker key={`chain-${c.id}`} coordinate={{ latitude: c.lat, longitude: c.lng }} anchor={{ x: 0.5, y: 0.5 }} zIndex={7}>
                <ChainMarkerView label={c.status} />
              </ViewMarker>
            ))}
          {(weatherChips ?? []).map((c) => {
            const mode = chipMode(c, zoom);
            return (
              <ViewMarker
                key={`wx-${c.index}`}
                redraw={`${mode}-${c.kind}-${c.temp_f}-${c.gust_mph}`}
                coordinate={{ latitude: c.lat, longitude: c.lng }}
                // The chip's tail tip sits on the route point; a dot is centred on it.
                anchor={mode === "chip" ? { x: 0.5, y: 1 } : { x: 0.5, y: 0.5 }}
                zIndex={mode === "chip" ? (c.warn ? 8 : 6) : 3}
                onPress={() => onWeatherChipPress?.(c)}
              >
                <WeatherChipView chip={c} mode={mode} clock={frameClock(c.eta) ?? ""} />
              </ViewMarker>
            );
          })}
        </>
      )}

      {props.ghost && (
        <ViewMarker key="ghost" coordinate={toCoord(props.ghost)} anchor={{ x: 0.5, y: 0.75 }} zIndex={11} redraw={props.ghost.label}>
          <GhostTruckView label={props.ghost.label} />
        </ViewMarker>
      )}
      <ViewMarker key="driver" coordinate={toCoord(driver)} anchor={{ x: 0.5, y: 0.5 }} zIndex={10} redraw={follow ? 0 : Math.round((heading ?? 0) / 10)}>
        <UserArrow heading={follow ? 0 : heading} />
      </ViewMarker>
    </MapView>
  );
});

export default MapLayer;
