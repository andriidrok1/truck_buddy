import { useEffect, useRef } from "react";

import { api } from "../api";
import { getLang, useLang, type Lang } from "../i18n";
import { useTripStore } from "../state/tripStore";

const trip = useTripStore.getState;

/**
 * The parking block of the active route (verdict, reason, entry and park tips) is the server's text in the language
 * of the request that fetched it. Asked again in `lang` (the X-Lang header follows the app's language); the server
 * answers from its cache, no new satellite check. The answer is dropped when the driver switched language again,
 * ended the route or got another destination or line meanwhile: that route brings its own parking.
 * Resolves true when the card was updated.
 */
export async function refreshParkingText(lang: Lang): Promise<boolean> {
  const cur = trip().active;
  // No parking on the card: nothing to re-word (and no reason to start a check the route did not ask for).
  if (!cur?.parking || cur.place.category === "service") return false;
  const { place, route } = cur;
  try {
    const pk = await api.parking(place, route.approach);
    const now = trip().active;
    if (getLang() !== lang || now?.place.id !== place.id || now.route !== route) return false;
    trip().setActiveParking(place.id, pk);
    return true;
  } catch {
    return false; // the old text stays; the route still works
  }
}

/**
 * The route card's parking text follows the app's language: it stayed Russian after a switch to English in the
 * truck profile (QA 2026-11-11). Runs once per change of language, never on the first mount; writing the answer to
 * the trip store does not touch the language, so it cannot call itself again.
 */
export function useParkingLanguage() {
  const lang = useLang();
  const shown = useRef(lang);
  useEffect(() => {
    if (shown.current === lang) return;
    shown.current = lang;
    void refreshParkingText(lang);
  }, [lang]);
}
