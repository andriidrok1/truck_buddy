import { StyleSheet, useWindowDimensions } from "react-native";
import MapView, { Polyline } from "react-native-maps";

import { useTheme } from "../theme";
import { ViewMarker } from "./MapLayerApple.native";
import { cameraDistanceForZoom, frameSpan, SAT_MAX_Z, SAT_MIN_Z, type ParkingSatMapProps } from "./mapTypes";
import { Pin } from "./Pin";
import { SpotBadge } from "./SpotBadge";

const toCoord = (lat: number, lng: number) => ({ latitude: lat, longitude: lng });

/**
 * Zoomable satellite map of the parking check frame: the map engine's own imagery (Apple on iOS, Google on
 * Android), free inside the app. It was Esri World Imagery tiles fetched without a key, which Esri's terms
 * don't allow in a commercial app (weather research 2026-11-11). The verdict itself is still made on the
 * server's image; spots are lat/lng, so they sit on either imagery (dates can differ by months).
 * Stays on react-native-maps whatever the main map's provider: MapLibre has no imagery here.
 */
export default function ParkingSatMap({ center, place, spots, tail }: ParkingSatMapProps) {
  const { colors } = useTheme();
  const { dLat, dLng } = frameSpan(center.lat);
  const { height } = useWindowDimensions();
  return (
    <MapView
      testID="parking-sat-map"
      style={StyleSheet.absoluteFill}
      mapType="satellite"
      initialRegion={{ ...toCoord(center.lat, center.lng), latitudeDelta: 2 * dLat, longitudeDelta: 2 * dLng }}
      zoomEnabled
      // The camera stops at SAT_MAX_Z (Apple and Google imagery stay sharp to about there) and SAT_MIN_Z keeps a
      // parking view from zooming out to the whole state. The zoom-level props
      // are what Google Maps honours; Apple Maps takes the same limits as camera distances.
      maxZoomLevel={SAT_MAX_Z}
      minZoomLevel={SAT_MIN_Z}
      cameraZoomRange={{
        minCenterCoordinateDistance: cameraDistanceForZoom(SAT_MAX_Z, center.lat, height),
        maxCenterCoordinateDistance: cameraDistanceForZoom(SAT_MIN_Z, center.lat, height),
      }}
      scrollEnabled
      rotateEnabled={false}
      pitchEnabled={false}
      showsPointsOfInterests={false}
      showsCompass={false}
      toolbarEnabled={false}
    >
      {tail.length > 1 && (
        <>
          <Polyline coordinates={tail.map(([a, b]) => toCoord(a, b))} strokeColor={colors.routeCasing} strokeWidth={9} zIndex={1} />
          <Polyline coordinates={tail.map(([a, b]) => toCoord(a, b))} strokeColor={colors.routeLine} strokeWidth={5} zIndex={2} />
        </>
      )}
      {place && (
        <ViewMarker coordinate={toCoord(place.lat, place.lng)} anchor={{ x: 0.5, y: 1 }} zIndex={3}>
          <Pin category={place.category} selected />
        </ViewMarker>
      )}
      {spots.map((s, i) => (
        <ViewMarker key={`${s.kind}-${i}`} coordinate={toCoord(s.lat, s.lng)} anchor={{ x: 0.5, y: 0.5 }} zIndex={4}>
          <SpotBadge kind={s.kind} size={30} />
        </ViewMarker>
      ))}
    </MapView>
  );
}
