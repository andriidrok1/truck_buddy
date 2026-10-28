// Expo config as code: identical to the former app.json except android.config.googleMaps.apiKey,
// which comes from the GOOGLE_MAPS_ANDROID_KEY env var (an EAS secret) so the key is never committed.
// Check the result with `npx expo config --type public`.
//
// EXPO_PUBLIC_MAP_PROVIDER=maplibre adds the MapLibre config plugin (native SDK on both platforms);
// the same variable makes the JS side pick that map (src/map/provider.ts). Without it the build is
// unchanged: react-native-maps only, and Expo Go keeps working.
import type { ExpoConfig } from "expo/config";

const mapLibre = process.env.EXPO_PUBLIC_MAP_PROVIDER?.trim().toLowerCase() === "maplibre";

const config: ExpoConfig = {
    "name": "TruckBuddy",
    "slug": "truckbuddy",
    "version": "1.0.0",
    "orientation": "portrait",
    "icon": "./assets/images/icon.png",
    "scheme": "truckbuddy",
    "userInterfaceStyle": "light",
    "ios": {
      "supportsTablet": false,
      "bundleIdentifier": "io.truckbuddy.app",
      "infoPlist": {
        "LSApplicationQueriesSchemes": [
          "comgooglemaps",
          "waze"
        ],
        "NSLocationWhenInUseUsageDescription": "TruckBuddy uses your location to find truck-friendly parking, fuel and repair shops near you and to plan truck-safe routes.",
        "NSMicrophoneUsageDescription": "TruckBuddy listens after you tap the copilot so you can ask for stops and directions by voice while driving.",
        "ITSAppUsesNonExemptEncryption": false
      },
      "buildNumber": "1",
      "config": {
        "usesNonExemptEncryption": false
      },
      "privacyManifests": {
        "NSPrivacyAccessedAPITypes": [
          {
            "NSPrivacyAccessedAPIType": "NSPrivacyAccessedAPICategoryUserDefaults",
            "NSPrivacyAccessedAPITypeReasons": [
              "CA92.1"
            ]
          }
        ],
        "NSPrivacyTracking": false,
        "NSPrivacyCollectedDataTypes": [
          {
            "NSPrivacyCollectedDataType": "NSPrivacyCollectedDataTypePreciseLocation",
            "NSPrivacyCollectedDataTypeLinked": false,
            "NSPrivacyCollectedDataTypeTracking": false,
            "NSPrivacyCollectedDataTypePurposes": [
              "NSPrivacyCollectedDataTypePurposeAppFunctionality"
            ]
          },
          {
            "NSPrivacyCollectedDataType": "NSPrivacyCollectedDataTypeAudioData",
            "NSPrivacyCollectedDataTypeLinked": false,
            "NSPrivacyCollectedDataTypeTracking": false,
            "NSPrivacyCollectedDataTypePurposes": [
              "NSPrivacyCollectedDataTypePurposeAppFunctionality"
            ]
          },
          {
            "NSPrivacyCollectedDataType": "NSPrivacyCollectedDataTypeOtherUserContent",
            "NSPrivacyCollectedDataTypeLinked": false,
            "NSPrivacyCollectedDataTypeTracking": false,
            "NSPrivacyCollectedDataTypePurposes": [
              "NSPrivacyCollectedDataTypePurposeAppFunctionality"
            ]
          },
          {
            "NSPrivacyCollectedDataType": "NSPrivacyCollectedDataTypeName",
            "NSPrivacyCollectedDataTypeLinked": false,
            "NSPrivacyCollectedDataTypeTracking": false,
            "NSPrivacyCollectedDataTypePurposes": [
              "NSPrivacyCollectedDataTypePurposeAppFunctionality",
              "NSPrivacyCollectedDataTypePurposeProductPersonalization"
            ]
          }
        ]
      }
    },
    "android": {
      "adaptiveIcon": {
        "foregroundImage": "./assets/images/adaptive-icon.png",
        "backgroundColor": "#F7F2EA"
      },
      "package": "io.truckbuddy.app",
      "permissions": [
        "ACCESS_FINE_LOCATION",
        "ACCESS_COARSE_LOCATION",
        "RECORD_AUDIO"
      ],
      "versionCode": 1,
      "blockedPermissions": [
        "android.permission.ACCESS_BACKGROUND_LOCATION"
      ],
      "config": {
        "googleMaps": {
          "apiKey": process.env.GOOGLE_MAPS_ANDROID_KEY
        }
      }
    },
    "web": {
      "bundler": "metro",
      "output": "single",
      "favicon": "./assets/images/favicon.png"
    },
    "plugins": [
      "expo-router",
      [
        "expo-splash-screen",
        {
          "image": "./assets/images/splash-icon.png",
          "imageWidth": 200,
          "resizeMode": "contain",
          "backgroundColor": "#F7F2EA"
        }
      ],
      "expo-font",
      "expo-image",
      "expo-secure-store",
      "expo-web-browser",
      "expo-status-bar",
      "@react-native-vector-icons/material-design-icons",
      [
        "expo-location",
        {
          "locationWhenInUsePermission": "TruckBuddy uses your location to find truck-friendly parking, fuel and repair shops near you and to plan truck-safe routes.",
          "locationAlwaysAndWhenInUsePermission": false,
          "locationAlwaysPermission": false,
          "isAndroidBackgroundLocationEnabled": false
        }
      ],
      [
        "expo-audio",
        {
          "microphonePermission": "TruckBuddy listens after you tap the copilot so you can ask for stops and directions by voice while driving.",
          "enableBackgroundPlayback": false,
          "enableBackgroundRecording": false
        }
      ],
      "expo-asset",
      ...(mapLibre ? (["@maplibre/maplibre-react-native"] as const) : [])
    ],
    "experiments": {
      "typedRoutes": true
    }
  };

export default config;
