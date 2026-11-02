import { ApiError, api } from "../api";
import { fetchProfile, isMissing, pushProfile } from "../data/profile";
import { DEFAULT_TRUCK, getTruckProfile } from "../hooks/useTruckProfile";

jest.mock("../api", () => {
  const actual = jest.requireActual("../api");
  return { ...actual, api: { ...actual.api, profile: jest.fn(), saveProfile: jest.fn() } };
});

const A = api as unknown as { profile: jest.Mock; saveProfile: jest.Mock };

test("no endpoint yet (404): the local profile stands, a save stays local", async () => {
  A.profile.mockRejectedValue(new ApiError(404, "/me/profile"));
  A.saveProfile.mockRejectedValue(new ApiError(404, "/me/profile"));
  expect(isMissing(new ApiError(404, "/x"))).toBe(true);
  expect(isMissing(new ApiError(500, "/x"))).toBe(false);
  const p = await fetchProfile();
  expect(p.height_in).toBe(DEFAULT_TRUCK.height_in);
  const saved = await pushProfile({ ...p, height_in: 169 });
  expect(saved.height_in).toBe(169);
  expect(getTruckProfile().height_in).toBe(169);
});

test("the server's profile wins and refreshes the local copy", async () => {
  A.profile.mockResolvedValue({ ...DEFAULT_TRUCK, height_in: 158, driver_name: "Ray" });
  const p = await fetchProfile();
  expect(p).toMatchObject({ height_in: 158, driver_name: "Ray" });
  expect(getTruckProfile().height_in).toBe(158);
});

test("a real failure propagates", async () => {
  A.profile.mockRejectedValue(new ApiError(500, "/me/profile"));
  await expect(fetchProfile()).rejects.toBeInstanceOf(ApiError);
});
