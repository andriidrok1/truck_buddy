import { renderHook } from "@testing-library/react-native";

import { api } from "../api";
import type { Guidance } from "../hooks/useGuidance";
import { useReroute } from "../hooks/useReroute";
import { useTripStore } from "../state/tripStore";
import type { ActiveRoute, LatLng, RouteInfo } from "../types";

jest.mock("../api", () => ({ api: { reroute: jest.fn(), route: jest.fn() }, isNoTruckRoute: () => false }));
jest.mock("../net/tripEvents", () => ({ tripLog: { log: jest.fn() } }));
jest.mock("../hooks/useOnline", () => ({ isOnline: () => true }));
jest.mock("../state/routeStore", () => ({ saveActiveRoute: jest.fn(), clearSavedRoute: jest.fn(), loadActiveRoute: jest.fn() }));

const DEG_PER_M = 1 / 111195;
/** A point `m` metres along a line that runs north to the destination 10 km away. */
const at = (m: number): LatLng => ({ lat: 38 + m * DEG_PER_M, lng: -121.5 });
const route = (summary: string): RouteInfo =>
  ({ geometry: [[at(0).lat, at(0).lng], [at(10000).lat, at(10000).lng]], summary, distance_m: 10000, duration_s: 600 }) as unknown as RouteInfo;
const active = { place: { id: "p1", name: "Harbor Truck Stop", category: "truck_stop", tags: [], ...at(10000) }, route: route("I-5 N") } as ActiveRoute;

const reset = jest.fn();
const guidance = { matchedIndex: 0, alongM: 0, distM: 80, thresholdM: 50, cum: [0, 10000], resetOffRoute: reset } as unknown as Guidance;

const setup = async (coords: LatLng) => {
  const say = jest.fn();
  const coordsRef = { current: coords };
  const h = await renderHook(() => useReroute({ coords, heading: null, sim: null }, coordsRef, { current: null }, say, jest.fn()));
  return { say, coordsRef, rerouteOffRoute: h.result.current.rerouteOffRoute };
};

beforeEach(() => {
  jest.clearAllMocks();
  useTripStore.getState().reset();
  useTripStore.getState().startRoute(active);
  useTripStore.getState().setDriving(true);
});

describe("rerouteOffRoute", () => {
  test("the /route fallback plans from where the truck is now, not from where the reroute began", async () => {
    const h = await setup(at(2000));
    (api.reroute as jest.Mock).mockImplementation(async () => {
      h.coordsRef.current = at(2400); // the truck kept moving while /reroute timed out
      throw new Error("timeout");
    });
    (api.route as jest.Mock).mockResolvedValue({ route: route("fresh") });
    await h.rerouteOffRoute(guidance);
    expect((api.route as jest.Mock).mock.calls[0][0]).toEqual(at(2400));
    expect(useTripStore.getState().active?.route.summary).toBe("fresh");
    expect(h.say).toHaveBeenCalledWith("Rerouting.", expect.anything());
  });

  test("no reroute and no 'Rerouting.' in the lot of the destination", async () => {
    const h = await setup(at(9880)); // 120 m from the pin, off the line, moving
    await h.rerouteOffRoute(guidance);
    expect(api.reroute).not.toHaveBeenCalled();
    expect(api.route).not.toHaveBeenCalled();
    expect(h.say).not.toHaveBeenCalled();
    expect(reset).toHaveBeenCalled(); // the tracker's cooldown does not outlive the lot
    expect(useTripStore.getState().active?.route.summary).toBe("I-5 N"); // the route is not ended either
  });
});
