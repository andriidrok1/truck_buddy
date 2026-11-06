import { useEffect, useState } from "react";

/**
 * Whether the map camera follows the truck (moved verbatim from app/index.tsx). `tracking` is the Follow button,
 * `overview` the whole-route look while driving, which goes back to following after 15 s.
 */
export function useFollowCamera(fitKey: string, driving: boolean) {
  const [overview, setOverview] = useState(false);
  // "Follow" button: the camera keeps the truck arrow in view until the driver drags the map.
  const [tracking, setTracking] = useState(false);
  const following = tracking || (driving && !overview);
  // A new view to show (route, options, overview) wins over the Follow button.
  const [fitSeen, setFitSeen] = useState(fitKey);
  if (fitSeen !== fitKey) {
    setFitSeen(fitKey);
    if (fitKey !== "init" && fitKey !== "gps") setTracking(false);
  }
  // Whole-route view while driving: back to following the truck after 15 s.
  useEffect(() => {
    if (!overview) return;
    const t = setTimeout(() => setOverview(false), 15000);
    return () => clearTimeout(t);
  }, [overview]);
  return { following, setOverview, setTracking };
}
