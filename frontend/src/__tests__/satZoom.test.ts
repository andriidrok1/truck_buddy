import { cameraDistanceForZoom, ESRI_MAX_NATIVE_Z, SAT_MAX_Z, SAT_MIN_Z } from "../components/mapTypes";

describe("parking satellite zoom limits", () => {
  test("the camera may go past the last native Esri zoom but only by upscaling", () => {
    expect(SAT_MAX_Z).toBeGreaterThan(ESRI_MAX_NATIVE_Z);
    expect(SAT_MIN_Z).toBeLessThan(ESRI_MAX_NATIVE_Z);
  });

  test("camera distance halves per zoom level and shrinks away from the equator", () => {
    const h = 844;
    const z15 = cameraDistanceForZoom(15, 0, h);
    expect(cameraDistanceForZoom(16, 0, h)).toBeCloseTo(z15 / 2, 6);
    expect(cameraDistanceForZoom(15, 60, h)).toBeCloseTo(z15 / 2, 6);
    // Sacramento, z21: tens of metres; z14: a few kilometres.
    expect(cameraDistanceForZoom(SAT_MAX_Z, 38.58, h)).toBeGreaterThan(50);
    expect(cameraDistanceForZoom(SAT_MAX_Z, 38.58, h)).toBeLessThan(200);
    expect(cameraDistanceForZoom(SAT_MIN_Z, 38.58, h)).toBeGreaterThan(5000);
    expect(cameraDistanceForZoom(SAT_MIN_Z, 38.58, h)).toBeLessThan(20000);
  });
});
