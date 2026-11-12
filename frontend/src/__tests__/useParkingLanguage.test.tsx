// The route card's parking text follows the app's language (QA 2026-11-11: it stayed Russian after a switch to
// English). The server's text is fetched again; these tests pin when, and when the answer is thrown away.
import { act, renderHook } from "@testing-library/react-native";

import { api } from "../api";
import { refreshParkingText, useParkingLanguage } from "../hooks/useParkingLanguage";
import { useLangStore } from "../i18n";
import { useTripStore } from "../state/tripStore";
import type { ActiveRoute, ParkingCheck, Place, RouteInfo } from "../types";

jest.mock("../api", () => {
  const actual = jest.requireActual("../api");
  return { ...actual, api: { ...actual.api, parking: jest.fn() } };
});

const parking = api.parking as unknown as jest.Mock;
const place = (id: string, category = "truck_stop") => ({ id, name: `Stop ${id}`, category, lat: 1, lng: 2, tags: [] }) as unknown as Place;
const route = (): RouteInfo => ({ geometry: [[0, 0], [0, 1]], distance_m: 1000, duration_s: 60, distance_mi: 0.6, duration_min: 1, approach: { street: "Main St" } } as unknown as RouteInfo);
const RU: ParkingCheck = { verdict: "listed", label: "Указана парковка для траков", reason: "", source: "place_data" };
const EN: ParkingCheck = { verdict: "listed", label: "Truck parking listed", reason: "", source: "place_data" };
const start = (over: Partial<ActiveRoute> = {}) => useTripStore.getState().startRoute({ place: place("p1"), route: route(), parking: RU, ...over } as ActiveRoute);
const shown = () => useTripStore.getState().active?.parking?.label;
const flush = async () => {
  for (let i = 0; i < 6; i++) await Promise.resolve();
};
const setLang = (lang: "en" | "ru") => act(async () => {
  useLangStore.setState({ lang });
  await flush();
});

beforeEach(() => {
  parking.mockReset();
  parking.mockResolvedValue(EN);
  useTripStore.getState().reset();
  useLangStore.setState({ lang: "ru" });
});
afterAll(() => useLangStore.setState({ lang: "en" }));

describe("useParkingLanguage", () => {
  test("nothing is fetched on the first mount, nor on a re-render in the same language", async () => {
    start();
    const { rerender } = await renderHook(() => useParkingLanguage());
    await rerender({});
    await flush();
    expect(parking).not.toHaveBeenCalled();
    expect(shown()).toBe(RU.label);
  });

  test("a change of language fetches the active route's parking once and puts it on the card", async () => {
    const active = start();
    await renderHook(() => useParkingLanguage());
    await setLang("en");
    expect(parking).toHaveBeenCalledTimes(1);
    expect(parking).toHaveBeenCalledWith(active.place, active.route.approach);
    expect(shown()).toBe(EN.label);
    // The store update re-renders nothing that would ask again.
    await flush();
    expect(parking).toHaveBeenCalledTimes(1);
  });

  test("no route, a route with no parking on the card, or a breakdown shop: no request", async () => {
    await renderHook(() => useParkingLanguage());
    await setLang("en");
    start({ parking: null });
    await setLang("ru");
    start({ place: place("shop", "service") });
    await setLang("en");
    expect(parking).not.toHaveBeenCalled();
  });
});

describe("refreshParkingText drops a stale answer", () => {
  let answer: (pk: ParkingCheck) => void = () => {};
  beforeEach(() => {
    parking.mockReturnValue(new Promise<ParkingCheck>((r) => (answer = r)));
    useLangStore.setState({ lang: "en" });
  });

  test("the destination changed while the request was out", async () => {
    start();
    const done = refreshParkingText("en");
    start({ place: place("p2"), parking: RU });
    answer(EN);
    expect(await done).toBe(false);
    expect(shown()).toBe(RU.label);
  });

  test("the same destination got a new line (a reroute brings its own parking)", async () => {
    start();
    const done = refreshParkingText("en");
    useTripStore.getState().applyRoute(useTripStore.getState().routeGen, route(), undefined, { ...RU, label: "от нового маршрута" });
    answer(EN);
    expect(await done).toBe(false);
    expect(shown()).toBe("от нового маршрута");
  });

  test("the route ended, or the driver switched the language back", async () => {
    start();
    let done = refreshParkingText("en");
    useTripStore.getState().endRoute();
    answer(EN);
    expect(await done).toBe(false);
    expect(useTripStore.getState().active).toBeNull();

    start();
    done = refreshParkingText("en");
    useLangStore.setState({ lang: "ru" });
    answer(EN);
    expect(await done).toBe(false);
    expect(shown()).toBe(RU.label);
  });

  test("a failed request leaves the old text", async () => {
    parking.mockRejectedValue(new Error("offline"));
    start();
    expect(await refreshParkingText("en")).toBe(false);
    expect(shown()).toBe(RU.label);
  });
});
