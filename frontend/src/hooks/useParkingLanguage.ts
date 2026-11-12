import { useEffect, useRef } from "react";

import { api } from "../api";
import { getLang, useLang, type Lang } from "../i18n";
import { useTripStore } from "../state/tripStore";
import type { ParkingCheck } from "../types";

const trip = useTripStore.getState;

/** The card holds a real verdict with words to translate. A check that failed (source "none") has none, and
 *  asking for it again would start a new satellite fetch and vision call on the server: never on a language switch. */
const hasVerdict = (pk: ParkingCheck | null | undefined): pk is ParkingCheck => !!pk && pk.source !== "none";

/**
 * The parking block of the active route (verdict, reason, entry and park tips) is the server's text in the language
 * of the request that fetched it. Asked again in `lang` (the X-Lang header follows the app's language). The verdict
 * comes from the server's cache, so no new satellite check; the tips of a satellite verdict may be translated by
 * the model the first time a language asks for them (a small paid call on the server, then cached).
 * The answer is dropped when the driver switched language again, ended the route or got another destination or
 * line meanwhile: that route brings its own parking. Resolves true when the card was updated.
 */
export async function refreshParkingText(lang: Lang): Promise<boolean> {
  const cur = trip().active;
  if (!cur || cur.place.category === "service" || !hasVerdict(cur.parking)) return false;
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
 * A switch while the route's first parking check is still out: that answer arrives in the old language, so the
 * refresh waits for it and runs once when it lands (one extra request at most).
 */
export function useParkingLanguage() {
  const lang = useLang();
  const placeId = useTripStore((s) => s.active?.place.id ?? null);
  const parking = useTripStore((s) => s.active?.parking ?? null);
  const shown = useRef(lang);
  /** The destination whose first parking answer is awaited, set by a language switch; null = nothing awaited. */
  const awaited = useRef<string | null>(null);

  useEffect(() => {
    if (shown.current === lang) return;
    shown.current = lang;
    const cur = trip().active;
    awaited.current = cur && !cur.parking && cur.place.category !== "service" ? cur.place.id : null;
    if (!awaited.current) void refreshParkingText(lang);
  }, [lang]);

  useEffect(() => {
    if (!awaited.current) return;
    if (placeId !== awaited.current) awaited.current = null; // another destination or none: it has its own check
    else if (parking) {
      awaited.current = null; // cleared before the request, so the refresh's own answer finds nothing awaited
      void refreshParkingText(getLang());
    }
  }, [placeId, parking]);
}
