// What the app does with the server's answer to a sign-in, before the token is stored.
import AsyncStorage from "@react-native-async-storage/async-storage";

import { DEFAULT_TRUCK, forgetTruckProfile, isOnboarded } from "../hooks/useTruckProfile";
import { ACCOUNT_KEY, getAccount, givenName, loadAccount, parseAccount, resetAccountForTests, setSignInNotice, signInNotice, welcomeBackDue, withAccountName } from "../state/account";
import { afterSignIn, prepareSignedIn } from "../state/afterSignIn";
import { forgetHowTo, howToDot, loadHowTo, shouldAutoShowHowTo } from "../state/howTo";

beforeEach(async () => {
  await AsyncStorage.clear();
  resetAccountForTests();
  await forgetTruckProfile();
  await forgetHowTo();
});

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
  test("onboarding is skipped, How to use does not open by itself, Welcome back is due, the account is kept for the profile screen", async () => {
    setSignInNotice("signed_out");
    const plan = await prepareSignedIn({ new_account: false, has_profile: true, name: "Mike Johnson", email: "mike@example.com" }, "apple");
    expect(plan).toBe("restore");
    expect(await isOnboarded()).toBe(true);
    const howTo = await loadHowTo();
    expect(howTo).toBe("seen");
    expect(shouldAutoShowHowTo(howTo, { focused: true, micCard: false, busy: false, open: false })).toBe(false);
    expect(howToDot(howTo)).toBe(false);
    expect(welcomeBackDue()).toBe(true);
    expect(getAccount()).toEqual({ name: "Mike Johnson", email: "mike@example.com", provider: "apple" });
    expect(JSON.parse((await AsyncStorage.getItem(ACCOUNT_KEY)) ?? "null")).toEqual({ name: "Mike Johnson", email: "mike@example.com", provider: "apple" });
    expect(signInNotice()).toBeNull(); // "You were signed out" is answered
  });

  test("killed before Open the map: next start there is no Welcome back, the driver is on the map, still onboarded", async () => {
    await prepareSignedIn({ new_account: false, has_profile: true, name: "Mike" }, "google");
    resetAccountForTests(); // a cold start: memory is gone, the disk stays
    expect(welcomeBackDue()).toBe(false);
    expect(await loadAccount()).toEqual({ name: "Mike", email: null, provider: "google" });
    expect(await isOnboarded()).toBe(true);
  });
});

describe("a new account", () => {
  test("onboarding stays ahead, How to use will be due after it, no Welcome back", async () => {
    const plan = await prepareSignedIn({ new_account: true, has_profile: false, name: "Olena Kovalenko", email: null }, "google");
    expect(plan).toBe("onboarding");
    expect(await isOnboarded()).toBe(false);
    expect(await loadHowTo()).toBeNull();
    expect(welcomeBackDue()).toBe(false);
    expect(getAccount()).toEqual({ name: "Olena Kovalenko", email: null, provider: "google" });
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
