import { useEffect, useState } from "react";
import { AppState } from "react-native";

/** Whether the app is in the foreground (AppState "active"). */
export function useAppActive(): boolean {
  const [appActive, setAppActive] = useState(AppState.currentState === "active");
  useEffect(() => {
    const sub = AppState.addEventListener("change", (s) => setAppActive(s === "active"));
    return () => sub.remove();
  }, []);
  return appActive;
}
