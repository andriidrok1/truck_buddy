// What the app does around the server's answer to a sign-in: before the token is stored, after it, and when a
// returning driver's profile lands.
import AsyncStorage from "@react-native-async-storage/async-storage";

import { ApiError, api } from "../api";
import { fetchProfile, resetProfile, restorePending, saveProfile, serverProfile, UNSYNCED_KEY } from "../data/profile";
import { DEFAULT_TRUCK, forgetTruckProfile, getTruckProfile, isOnboarded, setOnboarded } from "../hooks/useTruckProfile";
import { ACCOUNT_KEY, getAccount, givenName, loadAccount, parseAccount, resetAccountForTests, setSignInNotice, signInNotice, welcomeBackDue, withAccountName } from "../state/account";
import { queryClient } from "../query-client";
import { afterSignIn, finishSignedIn, prepareSignedIn } from "../state/afterSignIn";
import { forgetHowTo, howToDot, loadHowTo, markHowToSeen, shouldAutoShowHowTo } from "../state/howTo";
import { forgetHeight, isHeightConfirmed } from "../truck/heightConfirmed";

jest.mock("expo-secure-store", () => ({ getItemAsync: async () => null, setItemAsync: async () => {}, deleteItemAsync: async () => {} }));
jest.mock("../api", () => {
  const actual = jest.requireActual("../api");
  return { ...actual, api: { ...actual.api, profile: jest.fn(), saveProfile: jest.fn() } };
});
const A = api as unknown as { profile: jest.Mock; saveProfile: jest.Mock };
const settle = () => new Promise((r) => setTimeout(r, 0));

const RETURNING = { new_account: false, has_profile: true, name: "Mike Johnson", email: "mike@example.com" };
const NEW = { new_account: true, has_profile: false, name: "Olena Kovalenko", email: null };
const SERVER_TRUCK = { ...DEFAULT_TRUCK, height_in: 168, trailer_ft: 48, driver_name: "Mike" };

beforeEach(async () => {
  await AsyncStorage.clear();
  resetAccountForTests();
  A.profile.mockReset();
  A.saveProfile.mockReset().mockResolvedValue({});
  await resetProfile();
  await forgetTruckProfile();
  await forgetHowTo();
  await forgetHeight();
});

afterEach(() => queryClient.clear()); // its gc timer would keep jest alive

describe("the decision", () => {
  test("an account with a profile on the server is a returning driver; anything else goes through onboarding", () => {
    expect(afterSignIn({ has_profile: true, new_account: false })).toBe("restore");
    expect(afterSignIn({ has_profile: false, new_account: true })).toBe("onboarding");
    // Signed in before but never finished setup: nothing to bring back, onboarding again.
    expect(afterSignIn({ has_profile: false, new_account: false })).toBe("onboarding");
    // A profile wins whatever the "new" flag says (a fleet truck bound before the first sign-in ends).
    expect(afterSignIn({ has_profile: true, new_account: true })).toBe("restore");
  });
});

describe("a returning driver", () => {
  test("before the token: Welcome back is due and the profile is awaited; nothing says 'onboarded' yet", async () => {
    setSignInNotice("signed_out");
    expect(await prepareSignedIn(RETURNING)).toBe("restore");
    expect(welcomeBackDue()).toBe(true);
    expect(restorePending()).toBe(true);
    expect(signInNotice()).toBeNull(); // "You were signed out" is answered
    // Killed right here, before the token is stored: the phone is signed out and nothing on disk says otherwise.
    expect(await isOnboarded()).toBe(false);
    expect(await AsyncStorage.getItem(ACCOUNT_KEY)).toBeNull();
    expect(getAccount()).toBeNull();
  });

  test("after the token: the account is kept for the profile screen", async () => {
    await prepareSignedIn(RETURNING);
    await finishSignedIn(RETURNING, "apple");
    expect(getAccount()).toEqual({ name: "Mike Johnson", email: "mike@example.com", provider: "apple" });
    expect(JSON.parse((await AsyncStorage.getItem(ACCOUNT_KEY)) ?? "null")).toEqual({ name: "Mike Johnson", email: "mike@example.com", provider: "apple" });
  });

  test("the profile lands: the server's truck is in, onboarding is skipped, How to use does not open by itself", async () => {
    await prepareSignedIn(RETURNING);
    A.profile.mockResolvedValue({ profile: SERVER_TRUCK, source: "driver" });
    await fetchProfile();
    expect(restorePending()).toBe(false);
    expect(getTruckProfile()).toMatchObject({ height_in: 168, trailer_ft: 48, driver_name: "Mike" });
    expect(serverProfile()).toMatchObject({ height_in: 168 });
    expect(await isOnboarded()).toBe(true);
    const howTo = await loadHowTo();
    expect(howTo).toBe("seen");
    expect(shouldAutoShowHowTo(howTo, { focused: true, micCard: false, busy: false, open: false })).toBe(false);
    expect(howToDot(howTo)).toBe(false);
    expect(welcomeBackDue()).toBe(true); // until "Open the map"
    expect(A.saveProfile).not.toHaveBeenCalled(); // nothing is sent back
  });

  test("a save made while the server copy is on its way does not push the default truck over it: the server copy is the base, the edit goes on top", async () => {
    await prepareSignedIn(RETURNING);
    let land: (v: unknown) => void = () => {};
    A.profile.mockReturnValue(new Promise((resolve) => (land = resolve)));
    const pull = fetchProfile();
    await settle();
    // The height card on the map, or a setting: a save of the phone's default truck with one thing changed.
    await saveProfile({ ...getTruckProfile(), avoid_tolls: true });
    await settle();
    expect(A.saveProfile).not.toHaveBeenCalled(); // nothing goes out while the restore is pending
    expect(await AsyncStorage.getItem(UNSYNCED_KEY)).toBeNull();

    land({ profile: SERVER_TRUCK, source: "driver" });
    const merged = await pull;
    await settle();
    expect(merged).toMatchObject({ height_in: 168, trailer_ft: 48, driver_name: "Mike", avoid_tolls: true });
    expect(getTruckProfile()).toEqual(merged);
    expect(A.saveProfile).toHaveBeenCalledTimes(1);
    expect(A.saveProfile.mock.calls[0][0]).toMatchObject({ height_in: 168, trailer_ft: 48, avoid_tolls: true }); // the real truck, plus the edit
    expect(isHeightConfirmed()).toBe(true);
  });

  test("the pull fails (no signal): still pending, still nothing pushed, and the next pull finishes the job", async () => {
    await prepareSignedIn(RETURNING);
    A.profile.mockRejectedValue(new ApiError(503, "/me/profile"));
    await expect(fetchProfile()).rejects.toBeInstanceOf(ApiError);
    expect(restorePending()).toBe(true);
    expect(await isOnboarded()).toBe(false);
    await saveProfile({ ...getTruckProfile(), break_hours: 4 });
    expect(A.saveProfile).not.toHaveBeenCalled();
    A.profile.mockResolvedValue({ profile: SERVER_TRUCK, source: "driver" });
    expect(await fetchProfile()).toMatchObject({ height_in: 168, break_hours: 4 });
    expect(restorePending()).toBe(false);
  });

  test("the server has no profile after all (404): the driver is new: no Welcome back, onboarding ahead", async () => {
    await prepareSignedIn(RETURNING);
    await setOnboarded(); // whatever was there
    A.profile.mockRejectedValue(new ApiError(404, "/me/profile"));
    await fetchProfile();
    expect(restorePending()).toBe(false);
    expect(welcomeBackDue()).toBe(false);
    expect(await isOnboarded()).toBe(false);
    expect(await loadHowTo()).toBeNull();
    expect(serverProfile()).toBeNull();
  });

  test("a 404 after all leaves the phone's how-to flag: a screen already seen on this phone stays seen", async () => {
    await markHowToSeen();
    await prepareSignedIn(RETURNING);
    A.profile.mockRejectedValue(new ApiError(404, "/me/profile"));
    await fetchProfile();
    expect(await isOnboarded()).toBe(false);
    expect(await loadHowTo()).toBe("seen");
  });

  test("killed while the profile was on its way: after the restart the restore is still pending (Welcome back holds the app)", async () => {
    await prepareSignedIn(RETURNING);
    jest.resetModules();
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- a fresh module copy, as after a cold start
    const fresh = require("../data/profile") as typeof import("../data/profile");
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const freshApi = (require("../api") as typeof import("../api")).api as unknown as { profile: jest.Mock };
    freshApi.profile.mockResolvedValue({ profile: SERVER_TRUCK, source: "driver" });
    await fresh.fetchProfile();
    expect(fresh.restorePending()).toBe(false);
    expect(fresh.serverProfile()).toMatchObject({ height_in: 168 });
  });

  test("killed after the profile landed and before Open the map: next start there is no Welcome back, the driver is onboarded", async () => {
    await prepareSignedIn(RETURNING);
    await finishSignedIn({ ...RETURNING, name: "Mike", email: null }, "google");
    A.profile.mockResolvedValue({ profile: SERVER_TRUCK, source: "driver" });
    await fetchProfile();
    resetAccountForTests(); // a cold start: memory is gone, the disk stays
    expect(welcomeBackDue()).toBe(false);
    expect(await loadAccount()).toEqual({ name: "Mike", email: null, provider: "google" });
    expect(await isOnboarded()).toBe(true);
  });
});

describe("a new account", () => {
  test("onboarding stays ahead, How to use will be due after it, no Welcome back, nothing awaited", async () => {
    expect(await prepareSignedIn(NEW)).toBe("onboarding");
    await finishSignedIn(NEW, "google");
    expect(await isOnboarded()).toBe(false);
    expect(await loadHowTo()).toBeNull();
    expect(welcomeBackDue()).toBe(false);
    expect(restorePending()).toBe(false);
    expect(getAccount()).toEqual({ name: "Olena Kovalenko", email: null, provider: "google" });
  });

  test("a stale 'onboarded' left by an interrupted sign-in is cleared: a new driver always gets onboarding", async () => {
    await setOnboarded();
    await prepareSignedIn(NEW);
    expect(await isOnboarded()).toBe(false);
  });

  test("the name step starts with the provider's first name, and the driver's own name wins", () => {
    expect(withAccountName(DEFAULT_TRUCK, "Olena Kovalenko").driver_name).toBe("Olena");
    expect(withAccountName({ ...DEFAULT_TRUCK, driver_name: "  " }, "Olena Kovalenko").driver_name).toBe("Olena");
    expect(withAccountName({ ...DEFAULT_TRUCK, driver_name: "Lena" }, "Olena Kovalenko").driver_name).toBe("Lena");
    // Apple gave no name (a second authorization), or nobody is signed in to an account: nothing is made up.
    expect(withAccountName(DEFAULT_TRUCK, null)).toBe(DEFAULT_TRUCK);
    expect(withAccountName(DEFAULT_TRUCK, "   ")).toBe(DEFAULT_TRUCK);
    expect(withAccountName(DEFAULT_TRUCK, "x".repeat(60)).driver_name).toHaveLength(40); // the name field's limit
    expect(givenName("  Mike   Johnson ")).toBe("Mike");
    expect(givenName(undefined)).toBe("");
  });
});

test("what is stored under account_v1 is read back strictly", () => {
  expect(parseAccount({ name: " Mike ", email: "", provider: "apple" })).toEqual({ name: "Mike", email: null, provider: "apple" });
  expect(parseAccount({ name: "Mike", provider: "facebook" })).toBeNull();
  expect(parseAccount("apple")).toBeNull();
  expect(parseAccount(null)).toBeNull();
});
