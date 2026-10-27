// Fixtures are hand-written copies of what the backend sends. `satisfies` makes `tsc --noEmit`
// fail when a fixture drifts from src/types.ts (renamed or missing field, null where a string is
// required, geometry that is not a list of pairs), so a backend shape change shows up here before
// it shows up on a driver's phone.
//
// TypeScript widens JSON imports: "route_set" becomes `string`, `[lat, lng]` becomes `number[]`.
// `JsonShape<T>` applies the same widening to the target type so the structural check still
// works; the literal unions (status, category, verdict) are then pinned at runtime below.
import chat from "../../fixtures/responses/chat.json";
import options from "../../fixtures/responses/options.json";
import routeSet from "../../fixtures/responses/route_set.json";
import type { AgentResponse, Category } from "../types";

type JsonShape<T> = T extends string
  ? string
  : T extends number
    ? number
    : T extends boolean
      ? boolean
      : T extends readonly (infer U)[]
        ? JsonShape<U>[]
        : T extends object
          ? { [K in keyof T]: JsonShape<T[K]> }
          : T;

const routeSetResponse = routeSet satisfies JsonShape<AgentResponse>;
const optionsResponse = options satisfies JsonShape<AgentResponse>;
const chatResponse = chat satisfies JsonShape<AgentResponse>;

// `satisfies Record<..., true>` fails to compile when a union member is added to or removed from types.ts.
const STATUSES = Object.keys({
  options: true,
  route_set: true,
  chat: true,
  no_results: true,
  not_understood: true,
  researching: true,
  providers: true,
  error: true,
} satisfies Record<AgentResponse["status"], true>);

const CATEGORIES = Object.keys({
  truck_stop: true,
  hotel: true,
  rest_area: true,
  destination: true,
  service: true,
  walmart: true,
  cat_scale: true,
  truck_wash: true,
  weigh_station: true,
} satisfies Record<Category, true>);

describe("AgentResponse fixtures match src/types.ts", () => {
  test("every fixture uses a known status and category", () => {
    for (const r of [routeSetResponse, optionsResponse, chatResponse]) {
      expect(STATUSES).toContain(r.status);
      if (r.category !== null) expect(CATEGORIES).toContain(r.category);
    }
  });

  test("route_set carries a place and a route", () => {
    expect(routeSetResponse.status).toBe("route_set");
    expect(CATEGORIES).toContain(routeSetResponse.place.category);
    expect(routeSetResponse.route.geometry).toHaveLength(5);
    for (const point of routeSetResponse.route.geometry) expect(point).toHaveLength(2);
    expect(routeSetResponse.route.maneuvers[0].action).toBe("depart");
    expect(routeSetResponse.parking.verdict).toBe("listed");
  });

  test("options carries three places", () => {
    expect(optionsResponse.status).toBe("options");
    expect(optionsResponse.options).toHaveLength(3);
    for (const place of optionsResponse.options) expect(CATEGORIES).toContain(place.category);
    expect(optionsResponse.place).toBeNull();
  });

  test("chat carries a reply and nothing to navigate to", () => {
    expect(chatResponse.status).toBe("chat");
    expect(chatResponse.reply.length).toBeGreaterThan(0);
    expect(chatResponse.route).toBeNull();
  });
});
