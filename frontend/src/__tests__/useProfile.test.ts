import { ApiError, api } from "../api";
import { fetchProfile, isMissing, pushProfile, saveProfile, UNSYNCED_KEY } from "../data/profile";
import { DEFAULT_TRUCK, getTruckProfile } from "../hooks/useTruckProfile";
import { storage } from "../utils/storage";

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

test("a save made offline is sent on the next start instead of being undone by the older server copy", async () => {
  A.saveProfile.mockRejectedValueOnce(new ApiError(0, "/me/profile")); // no network
  await saveProfile({ ...DEFAULT_TRUCK, trailer_ft: 48 });
  await new Promise((r) => setTimeout(r, 0));
  expect(getTruckProfile().trailer_ft).toBe(48);
  expect(await storage.getItem(UNSYNCED_KEY, false)).toBe(true);

  A.profile.mockResolvedValue({ ...DEFAULT_TRUCK, trailer_ft: 53 }); // the server still has the old trailer
  A.saveProfile.mockImplementation(async (p) => p);
  const p = await fetchProfile();
  expect(p.trailer_ft).toBe(48);
  expect(A.saveProfile).toHaveBeenLastCalledWith(expect.objectContaining({ trailer_ft: 48 }));
  expect(await storage.getItem(UNSYNCED_KEY, false)).toBe(false);
});
