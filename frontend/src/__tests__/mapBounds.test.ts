import { corridorPack, estimateBytes, formatBytes, geometryHash, KB_PER_TILE, PACK_MAX_ZOOM, PACK_MIN_ZOOM, routeBounds, tileCount, tilesAtZoom, toLngLatBounds } from "../map/bounds";

// Sacramento to Reno-ish: ~1.2 degrees of longitude, ~1 degree of latitude.
const line: [number, number][] = [
  [38.58, -121.49],
  [38.9, -121.1],
  [39.2, -120.6],
  [39.53, -119.81],
];

describe("routeBounds", () => {
  test("bbox of the points plus the buffer on every side", () => {
    const b = routeBounds(line, 3)!;
    const dLat = 3 / 111.32;
    expect(b.sw.lat).toBeCloseTo(38.58 - dLat, 4);
    expect(b.ne.lat).toBeCloseTo(39.53 + dLat, 4);
    expect(b.sw.lng).toBeLessThan(-121.49);
    expect(b.ne.lng).toBeGreaterThan(-119.81);
    // The longitude buffer is wider in degrees than the latitude one (cos of ~39°).
    expect(-121.49 - b.sw.lng).toBeGreaterThan(dLat);
  });

  test("zero buffer is the exact bbox; no points is null", () => {
    expect(routeBounds(line, 0)).toEqual({ sw: { lat: 38.58, lng: -121.49 }, ne: { lat: 39.53, lng: -119.81 } });
    expect(routeBounds([], 3)).toBeNull();
    expect(toLngLatBounds(routeBounds(line, 0)!)).toEqual([-121.49, 38.58, -119.81, 39.53]);
  });
});

describe("geometryHash / corridorPack", () => {
  test("stable for the same geometry, different for a different one, 8 hex chars", () => {
    const h = geometryHash(line);
    expect(h).toMatch(/^[0-9a-f]{8}$/);
    expect(geometryHash(line.map(([a, b]) => [a, b]))).toBe(h);
    expect(geometryHash([...line, [39.6, -119.7]])).not.toBe(h);
    expect(geometryHash(line.slice().reverse())).not.toBe(h);
  });

  test("sub-metre jitter does not change the name (5 decimals)", () => {
    expect(geometryHash(line.map(([a, b]) => [a + 0.000001, b]))).toBe(geometryHash(line));
  });

  test("the pack is named by the hash and spans z6..z14", () => {
    const p = corridorPack({ geometry: line })!;
    expect(p.name).toBe(`route-${geometryHash(line)}`);
    expect(p.minZoom).toBe(PACK_MIN_ZOOM);
    expect(p.maxZoom).toBe(PACK_MAX_ZOOM);
    expect(p.bounds).toEqual(routeBounds(line));
    expect(corridorPack({ geometry: [] })).toBeNull();
  });
});

describe("tile maths", () => {
  const b = routeBounds(line, 0)!;

  test("one tile at z0, more as the zoom grows", () => {
    expect(tilesAtZoom(b, 0)).toBe(1);
    expect(tilesAtZoom(b, 6)).toBeGreaterThanOrEqual(1);
    expect(tilesAtZoom(b, 14)).toBeGreaterThan(tilesAtZoom(b, 10));
  });

  test("z14 count matches the bbox size: ~1.68° x ~0.95° at 39° north", () => {
    // Tile width at z14 = 360 / 16384 degrees of longitude.
    const cols = Math.floor(((-119.81 + 180) / 360) * 16384) - Math.floor(((-121.49 + 180) / 360) * 16384) + 1;
    expect(tilesAtZoom(b, 14) % cols).toBe(0);
    expect(tilesAtZoom(b, 14) / cols).toBeGreaterThanOrEqual(50);
  });

  test("the estimate is the tile count times 25 KB", () => {
    const n = tileCount(b, 6, 14);
    expect(n).toBe([6, 7, 8, 9, 10, 11, 12, 13, 14].reduce((s, z) => s + tilesAtZoom(b, z), 0));
    expect(estimateBytes(b)).toBe(n * KB_PER_TILE * 1024);
    // A Sacramento-Reno corridor at z14: a few thousand tiles, well under 200 MB.
    expect(estimateBytes(b)).toBeGreaterThan(20 * 1024 * 1024);
    expect(estimateBytes(b)).toBeLessThan(200 * 1024 * 1024);
  });

  test("a tiny box is still one tile per zoom", () => {
    const tiny = { sw: { lat: 38.5, lng: -121.5 }, ne: { lat: 38.5001, lng: -121.4999 } };
    expect(tileCount(tiny, 6, 14)).toBeGreaterThanOrEqual(9);
    expect(tileCount(tiny, 6, 14)).toBeLessThanOrEqual(36);
  });
});

describe("formatBytes", () => {
  test("KB / MB / GB", () => {
    expect(formatBytes(500)).toBe("1 KB");
    expect(formatBytes(900 * 1024)).toBe("900 KB");
    expect(formatBytes(1.5 * 1024 * 1024)).toBe("1.5 MB");
    expect(formatBytes(84 * 1024 * 1024)).toBe("84 MB");
    expect(formatBytes(1.2 * 1024 * 1024 * 1024)).toBe("1.2 GB");
  });
});
