// Keyboard helpers with a fallback for Expo Go.
// react-native-keyboard-controller needs its native module, which Expo Go does not ship; importing it
// there throws at startup. In a dev/preview/production build the real components are used; in Expo Go
// (or if the module fails to load) plain React Native views take their place, so screens still render.
// Loses in fallback mode: the keyboard-aware scroll moves only via KeyboardAvoidingView, and the
// sticky bottom bar follows the keyboard from RN's Keyboard events (iOS; Android resizes the
// window instead), which is good enough to test the flows on a phone.
import Constants, { ExecutionEnvironment } from "expo-constants";
import { useEffect, useState, type ComponentType } from "react";
import { Animated, Keyboard, KeyboardAvoidingView, Platform, ScrollView } from "react-native";

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

/**
 * Same contract as keyboard-controller's KeyboardStickyView: the view rides up with the keyboard,
 * `offset.opened` is added back once it is open (the sheet's own bottom inset is under the keyboard
 * by then). Android's window resizes, so the view moves on iOS only.
 */
const FallbackStickyView: AnyComponent = ({ offset, style, ...rest }) => {
  const opened = offset?.opened ?? 0;
  const closed = offset?.closed ?? 0;
  const [shift] = useState(() => new Animated.Value(closed));
  useEffect(() => {
    if (Platform.OS !== "ios") return;
    const move = (to: number, duration: number) => Animated.timing(shift, { toValue: to, duration, useNativeDriver: true }).start();
    const show = Keyboard.addListener("keyboardWillShow", (e) => move(-e.endCoordinates.height + opened, e.duration || 250));
    const hide = Keyboard.addListener("keyboardWillHide", (e) => move(closed, e.duration || 250));
    return () => {
      show.remove();
      hide.remove();
    };
  }, [shift, opened, closed]);
  return <Animated.View {...rest} style={[style, { transform: [{ translateY: shift }] }]} />;
};

export const KEYBOARD_CONTROLLER_AVAILABLE = native !== null;
export const KeyboardProvider: AnyComponent = native?.KeyboardProvider ?? FallbackProvider;
export const KeyboardAwareScrollView: AnyComponent = native?.KeyboardAwareScrollView ?? FallbackAwareScrollView;
export const KeyboardStickyView: AnyComponent = native?.KeyboardStickyView ?? FallbackStickyView;
