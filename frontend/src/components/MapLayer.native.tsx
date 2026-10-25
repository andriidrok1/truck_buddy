import { forwardRef, useEffect, useImperativeHandle, useRef, useState, type ReactNode } from "react";
import { StyleSheet } from "react-native";
import MapView, { Marker, Polyline } from "react-native-maps";

import { useTheme } from "../theme";
import type { LatLng } from "../types";
import type { MapLayerHandle, MapLayerProps } from "./mapTypes";
import { DestinationLabel, Pin, UserArrow } from "./Pin";

const toCoord = (p: LatLng) => ({ latitude: p.lat, longitude: p.lng });

/** Custom-view marker that stops tracking view changes once its icon has rendered. */
function ViewMarker({ children, ...rest }: React.ComponentProps<typeof Marker> & { children: ReactNode }) {
  const [tracks, setTracks] = useState(true);
  useEffect(() => {
    const t = setTimeout(() => setTracks(false), 800);
    return () => clearTimeout(t);
  }, []);
  return (
    <Marker tracksViewChanges={tracks} {...rest}>
      {children}
    </Marker>
  );
}

const MapLayer = forwardRef<MapLayerHandle, MapLayerProps>(function MapLayer(props, ref) {
  const { driver, heading, places, active, topInset, bottomInset, fitKey, fitPoints, onCenterChange, onPlacePress } = props;
  const { colors } = useTheme();
  const mapRef = useRef<MapView>(null);
  const padding = { top: topInset, bottom: bottomInset, left: 0, right: 0 };

  const recenter = () =>
    mapRef.current?.animateToRegion({ ...toCoord(driver), latitudeDelta: 0.015, longitudeDelta: 0.015 }, 450);

  useImperativeHandle(ref, () => ({ recenter }));

  useEffect(() => {
    if (fitPoints.length > 1) {
      mapRef.current?.fitToCoordinates(fitPoints.map(toCoord), {
        edgePadding: { top: 48, right: 56, bottom: 48, left: 56 },
        animated: true,
      });
    } else if (fitPoints.length === 1) {
      mapRef.current?.animateToRegion({ ...toCoord(fitPoints[0]), latitudeDelta: 0.015, longitudeDelta: 0.015 }, 450);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitKey]);

  return (
    <MapView
      ref={mapRef}
      testID="map-view"
      style={StyleSheet.absoluteFill}
      initialRegion={{ ...toCoord(driver), latitudeDelta: 0.015, longitudeDelta: 0.015 }}
      mapPadding={padding}
      showsUserLocation={false}
      showsCompass={false}
      showsMyLocationButton={false}
      toolbarEnabled={false}
      showsPointsOfInterests={false}
      onRegionChangeComplete={(r) => onCenterChange({ lat: r.latitude, lng: r.longitude })}
    >
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
        </>
      )}

      <ViewMarker key="driver" coordinate={toCoord(driver)} anchor={{ x: 0.5, y: 0.5 }} zIndex={10}>
        <UserArrow heading={heading} />
      </ViewMarker>
    </MapView>
  );
});

export default MapLayer;
