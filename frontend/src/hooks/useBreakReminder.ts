import { useEffect } from "react";

import { useTripStore } from "../state/tripStore";
import type { ActiveRoute } from "../types";
import { PRIORITY } from "../voice/queue";
import type { SayOptions } from "./useSpeechQueue";
import type { TruckProfile } from "./useTruckProfile";

const trip = useTripStore.getState;

/** Break reminder while a route is active (moved verbatim from app/index.tsx). */
export function useBreakReminder(active: ActiveRoute | null, truck: TruckProfile, say: (text: string, opts: SayOptions) => void) {
  useEffect(() => {
    if (!active || !truck.break_hours) return;
    const limitMs = truck.break_hours * 3600 * 1000;
    const t = setInterval(() => {
      const start = trip().routeStart;
      if (!start || Date.now() - start < limitMs) return;
      useTripStore.setState({ routeStart: Date.now() }); // remind again after the next interval
      const who = truck.driver_name ? `Hey ${truck.driver_name}, ` : "";
      const hrs = truck.break_hours === 1 ? "an hour" : `${truck.break_hours} hours`;
      const text = `${who}you've been driving for ${hrs}. Want me to find a rest area?`;
      say(text, { priority: PRIORITY.WEATHER, kind: "ambient", key: "break", ttlMs: 10 * 60 * 1000 });
    }, 60000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, truck.break_hours, truck.driver_name]);
}
