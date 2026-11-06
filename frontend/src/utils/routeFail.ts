import { isNoTruckRoute } from "../api";

/** Toast for a failed /route: the router's "no truck route" is a destination problem, not a retry. */
export function routeFailText(e: unknown): string {
  return isNoTruckRoute(e) ? "No truck route there from here. Try another destination or an address on a main road." : "Couldn't build a route. Try again.";
}
