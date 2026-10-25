import { Platform, StyleSheet } from "react-native";
import MapView, { Polyline, UrlTile } from "react-native-maps";

import { useTheme } from "../theme";
import { ViewMarker } from "./MapLayer.native";
import { ESRI_TILES, frameSpan, type ParkingSatMapProps } from "./mapTypes";
import { Pin } from "./Pin";
import { SpotBadge } from "./SpotBadge";

const toCoord = (lat: number, lng: number) => ({ latitude: lat, longitude: lng });

/** Zoomable satellite map of the parking check frame (Esri imagery, same as the verdict). */
export default function ParkingSatMap({ center, place, spots, tail }: ParkingSatMapProps) {
  const { colors } = useTheme();
  const { dLat, dLng } = frameSpan(center.lat);
  return (
    <MapView
      testID="parking-sat-map"
      style={StyleSheet.absoluteFill}
      // Android draws only the Esri tiles; iOS keeps satellite underneath (replaced by the tiles).
      mapType={Platform.OS === "android" ? "none" : "satellite"}
      initialRegion={{ ...toCoord(center.lat, center.lng), latitudeDelta: 2 * dLat, longitudeDelta: 2 * dLng }}
      zoomEnabled
      scrollEnabled
      rotateEnabled={false}
      pitchEnabled={false}
      showsPointsOfInterests={false}
      showsCompass={false}
      toolbarEnabled={false}
    >
      <UrlTile urlTemplate={ESRI_TILES} maximumZ={19} tileSize={256} shouldReplaceMapContent zIndex={0} />
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
