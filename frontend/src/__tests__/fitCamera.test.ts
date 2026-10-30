import { fitCamera, project, unproject } from "../map/fitCamera";

const SAC = { lat: 38.5816, lng: -121.4944 };
const ROADYS = { lat: 38.5796, lng: -121.5542 }; // ~5 km west
const RENO = { lat: 39.5296, lng: -119.8138 };

describe("fitCamera", () => {
  test("project/unproject round-trip", () => {
    const p = project(SAC, 12);
    const back = unproject(p.x, p.y, 12);
    expect(back.lat).toBeCloseTo(SAC.lat, 5);
    expect(back.lng).toBeCloseTo(SAC.lng, 5);
  });

  test("a short route in a phone-sized window gets a city zoom, centred between the points", () => {
    const cam = fitCamera([SAC, ROADYS], 412, 400);
    expect(cam.zoom).toBeGreaterThan(12.5);
    expect(cam.zoom).toBeLessThan(14.5);
    expect(cam.center.lng).toBeCloseTo((SAC.lng + ROADYS.lng) / 2, 3);
    expect(cam.center.lat).toBeCloseTo((SAC.lat + ROADYS.lat) / 2, 2);
    // the points really are inside the view at that zoom, margin included
    const [a, b] = [SAC, ROADYS].map((p) => project(p, cam.zoom));
    expect(Math.abs(a.x - b.x)).toBeLessThanOrEqual(412 - 2 * 56 + 0.5);
    expect(Math.abs(a.y - b.y)).toBeLessThanOrEqual(400 - 2 * 56 + 0.5);
  });

  test("a long route zooms out, a smaller window zooms out further, zoom is clamped", () => {
    const wide = fitCamera([SAC, RENO], 412, 600);
    expect(wide.zoom).toBeLessThan(9);
    expect(fitCamera([SAC, RENO], 412, 300).zoom).toBeLessThan(wide.zoom);
    expect(fitCamera([SAC, { lat: 38.5817, lng: -121.4945 }], 412, 600).zoom).toBe(16); // same spot: max zoom
    expect(fitCamera([{ lat: 60, lng: -150 }, { lat: 25, lng: -70 }], 100, 100).zoom).toBe(3); // whole continent: min zoom
  });

  test("one point and no points do not throw", () => {
    expect(fitCamera([SAC], 412, 600)).toEqual({ center: { lat: expect.closeTo(SAC.lat, 5), lng: expect.closeTo(SAC.lng, 5) }, zoom: 16 });
    expect(fitCamera([], 412, 600).zoom).toBe(3);
  });
});
