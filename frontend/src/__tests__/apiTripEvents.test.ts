import { api } from "../api";

const jsonResponse = (body: unknown) => ({
  ok: true,
  status: 200,
  headers: { get: (k: string) => (k.toLowerCase() === "content-type" ? "application/json" : null) },
  json: async () => body,
});

describe("api.tripEventsOf", () => {
  const realFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  test("hands back the rows of the backend's {events: [...]} answer (the tester screen maps over them)", async () => {
    const row = { id: 1, t: "2026-11-02T21:00:00Z", trip_id: "trip-1", kind: "route_started", data: {}, source: "app" };
    const fetchMock = jest.fn(async () => jsonResponse({ events: [row] }));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    expect(await api.tripEventsOf("trip-1")).toEqual([row]);
    const [url] = fetchMock.mock.calls[0] as unknown as [string];
    expect(url).toMatch(/\/api\/trips\/trip-1\/events$/);
  });
});
