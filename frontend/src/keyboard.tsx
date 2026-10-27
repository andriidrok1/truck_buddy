// Keyboard helpers with a fallback for Expo Go.
// react-native-keyboard-controller needs its native module, which Expo Go does not ship; importing it
// there throws at startup. In a dev/preview/production build the real components are used; in Expo Go
// (or if the module fails to load) plain React Native views take their place, so screens still render.
// Loses in fallback mode: the keyboard-aware scroll and the sticky bottom bar move with the keyboard
// only via KeyboardAvoidingView, which is good enough to test the flows on a phone.
import Constants, { ExecutionEnvironment } from "expo-constants";
import type { ComponentType } from "react";
import { KeyboardAvoidingView, Platform, ScrollView, View } from "react-native";

type AnyComponent = ComponentType<any>;

const inExpoGo = Constants.executionEnvironment === ExecutionEnvironment.StoreClient;

function load(): { KeyboardProvider: AnyComponent; KeyboardAwareScrollView: AnyComponent; KeyboardStickyView: AnyComponent } | null {
  if (inExpoGo || Platform.OS === "web") return null;
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const m = require("react-native-keyboard-controller");
    if (!m?.KeyboardProvider || !m?.KeyboardAwareScrollView || !m?.KeyboardStickyView) return null;
    return m;
  } catch {
    return null;
  }
}

const native = load();

const FallbackProvider: AnyComponent = ({ children }) => <>{children}</>;

const FallbackAwareScrollView: AnyComponent = ({ bottomOffset: _bottomOffset, ...rest }) => (
  <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
    <ScrollView {...rest} />
  </KeyboardAvoidingView>
);

const FallbackStickyView: AnyComponent = ({ offset: _offset, ...rest }) => <View {...rest} />;

export const KEYBOARD_CONTROLLER_AVAILABLE = native !== null;
export const KeyboardProvider: AnyComponent = native?.KeyboardProvider ?? FallbackProvider;
export const KeyboardAwareScrollView: AnyComponent = native?.KeyboardAwareScrollView ?? FallbackAwareScrollView;
export const KeyboardStickyView: AnyComponent = native?.KeyboardStickyView ?? FallbackStickyView;
