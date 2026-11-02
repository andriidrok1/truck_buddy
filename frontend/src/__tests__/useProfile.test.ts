import { ApiError, api } from "../api";
import { fetchProfile, isMissing, resetProfile, saveProfile, UNSYNCED_KEY } from "../data/profile";
import { DEFAULT_TRUCK, getTruckProfile, saveTruckProfile } from "../hooks/useTruckProfile";
import { storage } from "../utils/storage";

jest.mock("../api", () => {
  const actual = jest.requireActual("../api");
  return { ...actual, api: { ...actual.api, profile: jest.fn(), saveProfile: jest.fn() } };
});

const A = api as unknown as { profile: jest.Mock; saveProfile: jest.Mock };
const settle = () => new Promise((r) => setTimeout(r, 0));
const pending = () => storage.getItem<string | null>(UNSYNCED_KEY, null);

beforeEach(async () => {
  A.profile.mockReset();
  A.saveProfile.mockReset();
  await storage.removeItem(UNSYNCED_KEY);
  await saveTruckProfile(DEFAULT_TRUCK);
});

test("no endpoint yet (404): the local profile stands, a save stays local and leaves nothing pending", async () => {
  A.profile.mockRejectedValue(new ApiError(404, "/me/profile"));
  A.saveProfile.mockRejectedValue(new ApiError(404, "/me/profile"));
  expect(isMissing(new ApiError(404, "/x"))).toBe(true);
  expect(isMissing(new ApiError(500, "/x"))).toBe(false);
  expect((await fetchProfile()).height_in).toBe(DEFAULT_TRUCK.height_in);
  await saveProfile({ ...DEFAULT_TRUCK, height_in: 169 });
  await settle();
  expect(getTruckProfile().height_in).toBe(169);
  expect(await pending()).toBeNull();
});

test("the server's profile wins and refreshes the local copy", async () => {
  A.profile.mockResolvedValue({ ...DEFAULT_TRUCK, height_in: 158, driver_name: "Ray" });
  expect(await fetchProfile()).toMatchObject({ height_in: 158, driver_name: "Ray" });
  expect(getTruckProfile().height_in).toBe(158);
});

test("a real failure propagates", async () => {
  A.profile.mockRejectedValue(new ApiError(500, "/me/profile"));
  await expect(fetchProfile()).rejects.toBeInstanceOf(ApiError);
});

test("killed mid-request: the change is marked before the request, and the next start sends it instead of pulling", async () => {
  A.saveProfile.mockReturnValue(new Promise(() => {})); // never answers: the app dies first
  await saveProfile({ ...DEFAULT_TRUCK, trailer_ft: 48 });
  await settle();
  expect(await pending()).not.toBeNull();

  A.profile.mockResolvedValue({ ...DEFAULT_TRUCK, trailer_ft: 53 }); // the server still has the old trailer
  A.saveProfile.mockImplementation(async (p) => p);
  expect((await fetchProfile()).trailer_ft).toBe(48);
  expect(A.profile).not.toHaveBeenCalled();
  expect(A.saveProfile).toHaveBeenLastCalledWith(expect.objectContaining({ trailer_ft: 48 }));
  expect(await pending()).toBeNull();
});

test("an edit made while the server read is in flight is not undone by it", async () => {
  let answer: (p: unknown) => void = () => {};
  A.profile.mockReturnValue(new Promise((r) => (answer = r)));
  A.saveProfile.mockImplementation(async (p) => p);
  const read = fetchProfile();
  await settle();
  await saveProfile({ ...DEFAULT_TRUCK, trailer_ft: 48 });
  answer({ ...DEFAULT_TRUCK, trailer_ft: 53 });
  expect((await read).trailer_ft).toBe(48);
  expect(getTruckProfile().trailer_ft).toBe(48);
});

test("an older save finishing after a newer one does not clear the newer one's mark", async () => {
  let first: () => void = () => {};
  A.saveProfile.mockReturnValueOnce(new Promise<void>((r) => (first = r))).mockRejectedValueOnce(new ApiError(0, "/me/profile"));
  await saveProfile({ ...DEFAULT_TRUCK, trailer_ft: 48 });
  await saveProfile({ ...DEFAULT_TRUCK, trailer_ft: 40 }); // offline
  await settle();
  first();
  await settle();
  expect(JSON.parse((await pending()) as string).trailer_ft).toBe(40);
});

test("a profile the server refuses (422) is not sent again on every start", async () => {
  A.saveProfile.mockRejectedValue(new ApiError(422, "/me/profile"));
  await saveProfile({ ...DEFAULT_TRUCK, trailer_ft: 48 });
  await settle();
  await settle();
  expect(await pending()).toBeNull();
});

test("delete my data: the default truck, nothing pending", async () => {
  await storage.setItem(UNSYNCED_KEY, "{}");
  await saveTruckProfile({ ...DEFAULT_TRUCK, trailer_ft: 48, driver_name: "Ray" });
  await resetProfile();
  expect(getTruckProfile()).toEqual(DEFAULT_TRUCK);
  expect(await pending()).toBeNull();
});
