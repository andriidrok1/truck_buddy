import { act, renderHook } from "@testing-library/react-native";
import { AppState, type AppStateStatus } from "react-native";

import { useNetStore } from "../state/netStore";

import { ApiError, api } from "../api";
import { beginRestore, fetchProfile, isMissing, PROFILE_KEY, restorePending, resetProfile, saveProfile, setMidTripCheck, UNSYNCED_KEY, useProfileSync } from "../data/profile";
import { queryClient } from "../query-client";
import { forgetHeight, heightCardAllowed, isHeightConfirmed } from "../truck/heightConfirmed";
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
  A.profile.mockResolvedValue({ profile: { ...DEFAULT_TRUCK, height_in: 158, driver_name: "Ray" }, source: "driver" });
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

  A.profile.mockResolvedValue({ profile: { ...DEFAULT_TRUCK, trailer_ft: 53 }, source: "driver" }); // the server still has the old trailer
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
  answer({ profile: { ...DEFAULT_TRUCK, trailer_ft: 53 }, source: "driver" });
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

test("a fleet truck's stored sizes do not undo a trip's change; between trips they come back", async () => {
  await saveTruckProfile({ ...DEFAULT_TRUCK, trailer_ft: 48 }); // swapped for this run
  A.profile.mockResolvedValue({ profile: { ...DEFAULT_TRUCK, trailer_ft: 53, driver_name: "Sam" }, source: "vehicle" });
  setMidTripCheck(() => true);
  expect((await fetchProfile()).trailer_ft).toBe(48);
  setMidTripCheck(() => false);
  expect((await fetchProfile())).toMatchObject({ trailer_ft: 53, driver_name: "Sam" });
});

describe("a returning driver's truck on a new phone", () => {
  beforeEach(async () => {
    await forgetHeight();
    setMidTripCheck(() => false);
  });

  test("a stored height that is not the default is an answer the driver gave: the map does not ask again", async () => {
    A.profile.mockResolvedValue({ profile: { ...DEFAULT_TRUCK, height_in: 168 }, source: "driver" });
    await fetchProfile();
    expect(getTruckProfile().height_in).toBe(168);
    expect(isHeightConfirmed()).toBe(true);
  });

  test("the default height stays unconfirmed: a driver who skipped the truck step has it stored too, and the map must still ask", async () => {
    A.profile.mockResolvedValue({ profile: { ...DEFAULT_TRUCK, driver_name: "Ray" }, source: "driver" });
    await fetchProfile();
    expect(isHeightConfirmed()).toBe(false);
  });

  test("no profile on the server (404), or no answer: nothing is confirmed", async () => {
    A.profile.mockRejectedValue(new ApiError(404, "/me/profile"));
    await fetchProfile();
    expect(isHeightConfirmed()).toBe(false);
    A.profile.mockRejectedValue(new ApiError(503, "/me/profile"));
    await fetchProfile().catch(() => undefined);
    expect(isHeightConfirmed()).toBe(false);
  });
});

describe("when the server's profile is pulled (root layout)", () => {
  const tick = async () => {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  };
  afterEach(() => queryClient.clear());

  test("not before sign-in; right after it; and again for the next driver after a sign-out", async () => {
    A.profile.mockResolvedValue({ profile: { ...DEFAULT_TRUCK, height_in: 158 }, source: "driver" });
    const hook = await renderHook(({ signedIn }: { signedIn: boolean }) => useProfileSync(signedIn), { initialProps: { signedIn: false } });
    await tick();
    expect(A.profile).not.toHaveBeenCalled(); // a cold start on the sign-in screen: no request that can only be a 401

    await hook.rerender({ signedIn: true }); // the token just went in
    await tick();
    expect(A.profile).toHaveBeenCalledTimes(1);
    expect(getTruckProfile().height_in).toBe(158);
    expect(queryClient.getQueryData(PROFILE_KEY)).toMatchObject({ height_in: 158 });

    await hook.rerender({ signedIn: false }); // signed out
    queryClient.clear();
    await resetProfile();
    A.profile.mockResolvedValue({ profile: { ...DEFAULT_TRUCK, height_in: 150 }, source: "driver" });
    await hook.rerender({ signedIn: true }); // someone else signs in, same run
    await tick();
    expect(A.profile).toHaveBeenCalledTimes(2);
    expect(getTruckProfile().height_in).toBe(150);
  });

  test("a cold start with a token pulls once", async () => {
    A.profile.mockResolvedValue({ profile: DEFAULT_TRUCK, source: "driver" });
    const hook = await renderHook(() => useProfileSync(true));
    await tick();
    await hook.rerender({});
    await tick();
    expect(A.profile).toHaveBeenCalledTimes(1);
  });
});

describe("a returning driver's truck that has not landed (went to the map anyway)", () => {
  const tick = async () => {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  };
  afterEach(async () => {
    await resetProfile(); // writes the cache: the clear comes after, or its gc timer keeps jest alive
    queryClient.clear();
  });

  test("asked again on every return to the foreground and when the connection comes back, and no more once it landed", async () => {
    let onChange: (s: AppStateStatus) => void = () => {};
    const original = AppState.addEventListener;
    AppState.addEventListener = ((_t: string, h: (s: AppStateStatus) => void) => {
      onChange = h;
      return { remove: () => {} };
    }) as typeof AppState.addEventListener;
    useNetStore.setState({ net: false, hinted: false });
    await beginRestore();
    A.profile.mockRejectedValue(new ApiError(503, "/me/profile"));
    // The app's client retries a failed query on timers of its own; one attempt per trigger keeps the count exact.
    queryClient.setDefaultOptions({ queries: { retry: false } });
    const hook = await renderHook(() => useProfileSync(true));
    await tick();
    expect(A.profile).toHaveBeenCalledTimes(1);
    expect(restorePending()).toBe(true);

    await act(async () => onChange("active"));
    await tick();
    expect(A.profile).toHaveBeenCalledTimes(2);

    A.profile.mockResolvedValue({ profile: { ...DEFAULT_TRUCK, height_in: 158 }, source: "driver" });
    await act(async () => useNetStore.setState({ net: true }));
    await tick();
    expect(A.profile).toHaveBeenCalledTimes(3);
    expect(restorePending()).toBe(false);
    expect(getTruckProfile().height_in).toBe(158);

    await act(async () => onChange("active"));
    await tick();
    expect(A.profile).toHaveBeenCalledTimes(3); // landed: the usual start-up pull only, not one per foreground
    hook.unmount();
    queryClient.setDefaultOptions({ queries: {} });
    AppState.addEventListener = original;
  });
});

test("the height card stays away while a returning driver's truck is on its way, and while either flag is unread", () => {
  expect(heightCardAllowed(false, false)).toBe(true);
  expect(heightCardAllowed(false, true)).toBe(false); // the map opened "anyway": no default-height answer can be saved
  expect(heightCardAllowed(false, undefined)).toBe(false);
  expect(heightCardAllowed(null, false)).toBe(false);
  expect(heightCardAllowed(true, false)).toBe(false);
});
