import { useRef, type RefObject } from "react";

import { api, isNoTruckRoute } from "../api";
import { atRouteEnd, createRerouteGate } from "../geo/offroute";
import { tripLog } from "../net/tripEvents";
import { stopsAhead, useTripStore, viaOf } from "../state/tripStore";
import type { LatLng, Place, RouteInfo } from "../types";
import { routeFailText } from "../utils/routeFail";
import { PRIORITY } from "../voice/queue";
import type { useDriverLocation } from "./useDriverLocation";
import type { Guidance } from "./useGuidance";
import type { OfflineGuidance } from "./useOfflineGuidance";
import { isOnline } from "./useOnline";
import type { SayOptions } from "./useSpeechQueue";
import { t } from "../i18n";

const trip = useTripStore.getState;

type Loc = Pick<ReturnType<typeof useDriverLocation>, "coords" | "heading" | "sim">;

/** The map screen's route re-planning, moved verbatim from app/index.tsx. The handlers are rebuilt each render. */
export function useReroute(
  loc: Loc,
  coordsRef: RefObject<LatLng>,
  offlineRef: RefObject<OfflineGuidance | null>,
  say: (text: string, opts: SayOptions) => void,
  showToast: (text: string, kind?: "error" | "info") => void,
) {
  /**
   * A route that landed while driving and breaks a truck restriction (or needs a permit) is said out loud:
   * the card with the red banner is not on screen then, and the turn calls would otherwise just carry on.
   */
  const warnRestricted = (route: RouteInfo) => {
    if (!route.restricted || !trip().driving) return;
    const line = route.notices?.[0] ?? "";
    // Whole templates through t(): the queue's own t() left "Внимание: this route needs a permit…" half English (S11).
    const text =
      route.restricted === "legal"
        ? line
          ? t("Heads up: {line}", { line })
          : t("Heads up: this route needs a permit for your truck.")
        : t("Heads up, the new route isn't cleared for your truck. {line} Watch the signs.", { line });
    say(text.replace(/\s+/g, " ").trim(), { priority: PRIORITY.MANEUVER, kind: "system", key: "route-restricted", ttlMs: 30000 });
  };

  const rerouting = useRef(false);
  const rerouteGate = useRef(createRerouteGate()).current;
  /**
   * The tracker says the truck left the route: ask the engine to resume the plan from here
   * (/reroute with the handle, section and metres into it), or plan afresh when that fails.
   * One at a time; the new route only lands if the destination is still the same.
   * Not for a tester parked on a fake spot (no trace = nothing moves), not before "Go", and not at the
   * destination: a truck circling the lot has arrived, a new plan to where it already is helps nobody.
   */
  const rerouteOffRoute = async (g: Guidance) => {
    const cur = trip().active;
    if (!cur || rerouting.current || !trip().driving) return;
    if (loc.sim && !loc.sim.trace) return;
    const end = cur.route.geometry[cur.route.geometry.length - 1];
    if (atRouteEnd(loc.coords, cur.place, end ? { lat: end[0], lng: end[1] } : null)) {
      // The tracker's 30 s cooldown is dropped: a truck that leaves the lot off the line is picked up at once.
      g.resetOffRoute();
      return;
    }
    if (!rerouteGate.allow(Date.now())) return;
    // No signal: no /reroute. Point the driver back at the loaded line instead; the reroute
    // runs as soon as the signal is back (useOfflineGuidance calls this again).
    if (!isOnline()) {
      offlineRef.current?.onOffRoute(g);
      return;
    }
    rerouting.current = true;
    const route = cur.route;
    const starts = route.section_starts ?? [];
    let sectionIndex = 0;
    for (let i = 0; i < starts.length; i++) if (starts[i] <= g.matchedIndex) sectionIndex = i;
    const sectionStart = starts[sectionIndex] ?? 0;
    const traveledM = Math.max(0, g.alongM - (g.cum[sectionStart] ?? 0));
    say("Rerouting.", { priority: PRIORITY.MANEUVER, kind: "system", key: "reroute", ttlMs: 20000 });
    const gen = trip().routeGen;
    const event = { cause: "off_route", dist_m: Math.round(g.distM), threshold_m: g.thresholdM, handle: !!route.handle, section_index: sectionIndex };
    // /reroute gets the whole via list and the section index and drops the stops behind on its side; the
    // fallback /route and the stop chips only get what is still ahead.
    const ahead = cur.stops?.length ? cur.stops.slice(Math.min(sectionIndex, cur.stops.length)) : cur.stops;
    let fallback = false;
    try {
      let res;
      try {
        res = await api.reroute({
          lat: loc.coords.lat,
          lng: loc.coords.lng,
          place: cur.place,
          handle: route.handle ?? null,
          heading: loc.heading ?? null,
          section_index: sectionIndex,
          traveled_m: Math.round(traveledM),
          via: route.via ?? [],
          trip_id: cur.trip_id ?? null,
        });
      } catch (e) {
        console.log(`[reroute] /reroute failed (${String(e)}), planning from scratch`);
        fallback = true;
        res = await api.route(loc.coords, cur.place, false, (route.via ?? []).slice(sectionIndex), cur.trip_id ?? null);
      }
      // Only onto the route the truck left: a stop added or a route picked while this ran is newer.
      const applied = trip().applyRoute(gen, res.route, ahead, res.parking);
      if (applied) warnRestricted(res.route);
      tripLog.log("reroute", { ...event, ok: true, fallback, applied, restricted: res.route.restricted ?? null }, cur.trip_id);
      g.resetOffRoute();
    } catch {
      tripLog.log("reroute", { ...event, ok: false, fallback }, cur.trip_id);
      showToast("Couldn't reroute. Follow the map for now and watch the truck signs.");
    } finally {
      rerouting.current = false;
      rerouteGate.done(Date.now());
    }
  };

  /** Re-plans the active route with the current truck profile (after a voice change) or to the entrance;
   * the stops on the way stay (they used to vanish from the line while their chips stayed on screen). */
  const reroute = async (cause: "profile" | "entrance" | "resume", failMsg = "Couldn't update the route. Try again."): Promise<boolean> => {
    const cur = trip().active;
    const gen = trip().routeGen;
    if (!cur) return false;
    try {
      // From where the truck is now, not where it was when the driver tapped the mic, and only through the
      // stops still ahead (a fresh /route knows no section index to drop the ones already made).
      const stops = stopsAhead(cur.route, cur.stops, coordsRef.current);
      const res = await api.route(coordsRef.current, cur.place, false, viaOf(stops), cur.trip_id ?? null);
      // The route must still be the one this started from (the store's generation): a stop added or an
      // off-route reroute that landed meanwhile is newer than this plan. The caller learns whether it landed.
      const applied = trip().applyRoute(gen, res.route, stops, res.parking);
      if (applied) warnRestricted(res.route);
      tripLog.log("reroute", { cause, ok: true, applied, restricted: res.route.restricted ?? null }, cur.trip_id);
      return applied;
    } catch (e) {
      tripLog.log("reroute", { cause, ok: false, no_route: isNoTruckRoute(e) }, cur.trip_id);
      if (trip().routeGen === gen) showToast(isNoTruckRoute(e) ? routeFailText(e) : failMsg);
      return false;
    }
  };

  /** "×" on a stop chip: re-plan through the remaining stops (plain /route when none are left). */
  const onRemoveStop = async (stop: Place) => {
    const cur = trip().active;
    const gen = trip().routeGen;
    if (!cur) return;
    const stops = stopsAhead(cur.route, cur.stops, loc.coords).filter((s) => s.id !== stop.id);
    const via = viaOf(stops);
    try {
      const res = via.length
        ? await api.reroute({
            lat: loc.coords.lat,
            lng: loc.coords.lng,
            place: cur.place,
            handle: null, // the old handle pins the old stops; a changed list must route fresh
            heading: loc.heading ?? null,
            section_index: 0,
            traveled_m: 0,
            via,
            trip_id: cur.trip_id ?? null,
          })
        : await api.route(coordsRef.current, cur.place, false, [], cur.trip_id ?? null);
      if (trip().applyRoute(gen, res.route, stops, res.parking)) {
        tripLog.log("stop_removed", { stops: stops.length, duration_s: res.route.duration_s }, cur.trip_id);
      }
    } catch {
      showToast("Couldn't update the stops. Try again.");
    }
  };

  return { rerouteOffRoute, reroute, onRemoveStop };
}
