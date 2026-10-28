import { useState } from "react";
import { PanResponder, type PanResponderCallbacks, type PanResponderInstance } from "react-native";

/**
 * A PanResponder built once, on the first render. `create` returns the callbacks (and may keep
 * gesture-local state in its closure); they run on touches only, so they may read the
 * component's refs. Inline `useState(() => PanResponder.create(...))` trips the React Compiler,
 * which cannot tell those callbacks apart from render code.
 */
export function usePanResponder(create: () => PanResponderCallbacks): PanResponderInstance {
  const [pan] = useState(() => PanResponder.create(create()));
  return pan;
}
