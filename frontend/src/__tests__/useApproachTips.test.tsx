import { renderHook } from "@testing-library/react-native";

import { useApproachTips } from "../hooks/useApproachTips";
import type { ActiveRoute, ParkingCheck } from "../types";

const parking = { verdict: "likely", label: "x", reason: "x", source: "satellite", entry_tip: "Enter from El Centro Rd" } as unknown as ParkingCheck;
const trip = (tripId: string): ActiveRoute =>
  ({ place: { id: "p1", name: "Pilot", category: "truck_stop", lat: 38.55, lng: -121.42, tags: [] }, route: { geometry: [] }, parking, trip_id: tripId }) as unknown as ActiveRoute;

type Props = { active: ActiveRoute; remainingM: number };

describe("useApproachTips", () => {
  test("half a mile out, once per trip; a second trip to the same place gets them again", async () => {
    const say = jest.fn();
    const first = trip("trip-1");
    const h = await renderHook((p: Props) => useApproachTips(true, p.active, p.remainingM, say), { initialProps: { active: first, remainingM: 5000 } });
    expect(say).not.toHaveBeenCalled();

    await h.rerender({ active: first, remainingM: 700 });
    expect(say).toHaveBeenCalledTimes(1);
    expect(say.mock.calls[0][0]).toContain("El Centro Rd");
    // closer, and a reroute on the way in (a new object for the same trip): not again
    await h.rerender({ active: first, remainingM: 300 });
    await h.rerender({ active: { ...first }, remainingM: 250 });
    expect(say).toHaveBeenCalledTimes(1);

    // the same truck stop the next day
    const second = trip("trip-2");
    await h.rerender({ active: second, remainingM: 5000 });
    expect(say).toHaveBeenCalledTimes(1);
    await h.rerender({ active: second, remainingM: 600 });
    expect(say).toHaveBeenCalledTimes(2);
  });
});
