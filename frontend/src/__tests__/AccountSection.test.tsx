// The account part of the truck screen (Figma f5Uha6gopQHRmcGlMXdCD9, 135:2, screens 8 to 10): who is signed in, and
// what Sign out, Sign out of all phones and Delete account do on the screen. The work itself is tested in
// accountActions.test.ts; here it is stubbed.
import AsyncStorage from "@react-native-async-storage/async-storage";
import { act, fireEvent, render, screen } from "@testing-library/react-native";

import { setToken } from "../auth";
import { AccountActions } from "../components/AccountActions";
import { AccountSection, initialOf } from "../components/AccountSection";
import { ARM_MS } from "../components/ConfirmSheet";
import { beginRestore, cancelRestore, saveProfile, UNSYNCED_KEY } from "../data/profile";
import { DEFAULT_TRUCK } from "../hooks/useTruckProfile";
import { setLang } from "../i18n";
import { queryClient } from "../query-client";
import { resetAccountForTests, saveAccount } from "../state/account";
import { deleteAccount, signOut, signOutEverywhere } from "../state/accountActions";
import { deleteMyData, signedOutReset } from "../state/firstLaunch";

jest.mock("react-native-reanimated", () => {
  const { View } = jest.requireActual<typeof import("react-native")>("react-native");
  return {
    __esModule: true,
    default: { View },
    useSharedValue: (v: unknown) => {
      const sv = { value: v, get: () => sv.value, set: (n: unknown) => void (sv.value = n) };
      return sv;
    },
    useAnimatedStyle: (f: () => object) => f(),
    withSpring: (v: unknown) => v,
    withTiming: (v: unknown) => v,
  };
});
jest.mock("react-native-safe-area-context", () => ({ useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }) }));
jest.mock("expo-secure-store", () => ({ getItemAsync: async () => null, setItemAsync: async () => {}, deleteItemAsync: async () => {} }));
jest.mock("../state/accountActions", () => ({ signOut: jest.fn(async () => {}), signOutEverywhere: jest.fn(async () => "done"), deleteAccount: jest.fn() }));
jest.mock("../state/firstLaunch", () => ({ ...jest.requireActual("../state/firstLaunch"), deleteMyData: jest.fn(async () => true), signedOutReset: jest.fn(async () => []) }));
jest.mock("../api", () => {
  const actual = jest.requireActual("../api");
  return { ...actual, api: { ...actual.api, saveProfile: jest.fn(async (p: unknown) => p) } };
});

const saveMock = (jest.requireMock("../api") as { api: { saveProfile: jest.Mock } }).api.saveProfile;
const mocks = { signOut: signOut as jest.Mock, everywhere: signOutEverywhere as jest.Mock, del: deleteAccount as jest.Mock, delMyData: deleteMyData as jest.Mock, reset: signedOutReset as jest.Mock };

let now = 1_000_000;
const settle = async () => {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
};
/** Past the sheet's guard against a double tap. */
const later = () => {
  now += ARM_MS + 50;
};
const press = async (id: string) => {
  await fireEvent.press(screen.getByTestId(id));
  await settle();
};

beforeEach(async () => {
  jest.spyOn(Date, "now").mockImplementation(() => now);
  Object.values(mocks).forEach((m) => m.mockClear());
  resetAccountForTests();
  await AsyncStorage.clear();
  setLang("en");
  await cancelRestore();
});
afterEach(() => {
  jest.restoreAllMocks();
});
afterAll(() => queryClient.clear());

const asAccount = async (provider: "apple" | "google", name: string | null = "Mike Kovalenko", email: string | null = "mike@example.com") => {
  await setToken("ACC", "driver", "account", "REF");
  await saveAccount({ name, email, provider });
};

describe("AccountSection", () => {
  test("Apple: the initial, the name, the email and the provider", async () => {
    await asAccount("apple");
    await render(<AccountSection />);
    await settle();
    expect(screen.getByTestId("account-initial")).toHaveTextContent("M");
    expect(screen.getByTestId("account-name")).toHaveTextContent("Mike Kovalenko");
    expect(screen.getByTestId("account-sub")).toHaveTextContent("mike@example.com · Signed in with Apple");
  });

  test("Google in Russian; Apple's hidden name falls back to the driver's name", async () => {
    setLang("ru");
    await asAccount("google", null, "x1@privaterelay.appleid.com");
    await render(<AccountSection driverName="Олег" />);
    await settle();
    expect(screen.getByTestId("account-name")).toHaveTextContent("Олег");
    expect(screen.getByTestId("account-initial")).toHaveTextContent("О");
    expect(screen.getByTestId("account-sub")).toHaveTextContent("x1@privaterelay.appleid.com · Вход через Google");
  });

  test("an access code: no email, no initial", async () => {
    await setToken("CODE", "tester", "code", null);
    await render(<AccountSection />);
    await settle();
    expect(screen.getByTestId("account-name")).toHaveTextContent("Access code");
    expect(screen.getByTestId("account-sub")).toHaveTextContent("Signed in with an access code");
    expect(screen.queryByTestId("account-initial")).toBeNull();
  });

  test("an account sign-in whose account_v1 is not there yet: Your account, never Access code (L1)", async () => {
    await setToken("ACC", "driver", "account", "R1");
    await render(<AccountSection driverName="Mike" />);
    await settle();
    expect(screen.getByTestId("account-name")).toHaveTextContent("Mike");
    expect(screen.queryByText(/Access code/)).toBeNull();
    await render(<AccountSection />);
    await settle();
    expect(screen.getByTestId("account-name")).toHaveTextContent("Your account");
    expect(screen.queryByText(/access code/i)).toBeNull();
  });

  test("Synced only when nothing waits for the server: not with a change unsent, not while a restore is pending", async () => {
    await asAccount("google");
    await render(<AccountSection />);
    await settle();
    expect(screen.getByTestId("account-synced")).toBeTruthy();

    saveMock.mockRejectedValueOnce(new TypeError("Network request failed")); // offline: the change waits
    await act(async () => {
      await saveProfile({ ...DEFAULT_TRUCK, height_in: 160 });
    });
    await settle();
    expect(screen.queryByTestId("account-synced")).toBeNull();
    expect(await AsyncStorage.getItem(UNSYNCED_KEY)).not.toBeNull();

    await act(async () => {
      await saveProfile({ ...DEFAULT_TRUCK, height_in: 161 }); // back online: the server takes it
    });
    await settle();
    expect(screen.getByTestId("account-synced")).toBeTruthy();

    await act(async () => {
      await beginRestore(); // a returning driver's truck is on its way
    });
    expect(screen.queryByTestId("account-synced")).toBeNull();
  });

  test("the initial of a name that starts with a space or a lower-case letter", () => {
    expect(initialOf("  mike")).toBe("M");
    expect(initialOf("")).toBe("");
    expect(initialOf(null)).toBe("");
  });
});

describe("AccountActions, an account", () => {
  const router = { canDismiss: () => true, dismissAll: jest.fn() };
  const open = async () => {
    await asAccount("apple");
    await render(<AccountActions router={router} />);
    await settle();
  };

  test("Sign out asks first, with what stays, then signs out", async () => {
    await open();
    expect(screen.queryByTestId("delete-my-data")).toBeNull();
    await press("account-sign-out");
    expect(screen.getByTestId("sign-out-sheet-message")).toHaveTextContent("Your truck, places and history stay in your account. This phone goes back to an empty start.");
    later();
    await press("sign-out-sheet-confirm");
    expect(mocks.signOut).toHaveBeenCalledTimes(1);
  });

  test("Cancel signs nobody out", async () => {
    await open();
    await press("account-sign-out");
    await press("sign-out-sheet-cancel");
    expect(mocks.signOut).not.toHaveBeenCalled();
    expect(screen.queryByTestId("sign-out-sheet")).toBeNull();
  });

  test("Sign out of all phones: confirmed, then done; a failure says so and keeps the driver signed in", async () => {
    await open();
    await press("account-sign-out-all");
    later();
    await press("sign-out-all-sheet-confirm");
    expect(mocks.everywhere).toHaveBeenCalledTimes(1);

    mocks.everywhere.mockResolvedValueOnce("failed");
    await press("account-sign-out-all");
    later();
    await press("sign-out-all-sheet-confirm");
    expect(screen.getByTestId("account-error")).toHaveTextContent("Couldn't reach the server. You're still signed in everywhere. Try again.");

    mocks.everywhere.mockResolvedValueOnce("unconfirmed");
    await press("account-sign-out-all");
    later();
    await press("sign-out-all-sheet-confirm");
    expect(screen.getByTestId("account-error")).toHaveTextContent("We couldn't confirm that every phone was signed out. Check your connection and try again.");
  });

  test("Delete takes two steps; a double tap does not go through the second", async () => {
    await open();
    mocks.del.mockResolvedValue({ ok: true, appleRevoked: true });
    await press("account-delete");
    expect(screen.getByTestId("delete-account-sheet-message")).toHaveTextContent(/truck profile, preferences, history, trips and ledger/);
    later();
    await press("delete-account-sheet-confirm");
    expect(mocks.del).not.toHaveBeenCalled();
    await press("delete-account-final-confirm"); // the same instant: the second half of a double tap
    expect(mocks.del).not.toHaveBeenCalled();
    later();
    await press("delete-account-final-confirm");
    expect(mocks.del).toHaveBeenCalledTimes(1);
  });

  test("409: the server's reason is shown (in Russian on a Russian phone)", async () => {
    setLang("ru");
    await open();
    mocks.del.mockResolvedValue({
      ok: false,
      reason: "refused",
      code: "last_owner",
      message: "This account is the only owner of a fleet that still has drivers or a stored key. Write to support@truckbuddy.io to close the fleet or hand it over.",
    });
    await press("account-delete");
    later();
    await press("delete-account-sheet-confirm");
    later();
    await press("delete-account-final-confirm");
    expect(screen.getByTestId("account-error")).toHaveTextContent(/support@truckbuddy\.io/);
    expect(screen.getByTestId("account-error")).toHaveTextContent(/флот/);
  });

  test.each([
    ["not_removed", "Couldn't delete your account. Nothing was removed. Try again."],
    ["failed", "Couldn't delete your account. Try again."],
  ])("%s: %s", async (reason, text) => {
    await open();
    mocks.del.mockResolvedValue({ ok: false, reason });
    await press("account-delete");
    later();
    await press("delete-account-sheet-confirm");
    later();
    await press("delete-account-final-confirm");
    expect(screen.getByTestId("account-error")).toHaveTextContent(text);
  });

  test("no answer and no confirmation: we couldn't confirm", async () => {
    await open();
    mocks.del.mockResolvedValue({ ok: false, reason: "unconfirmed" });
    await press("account-delete");
    later();
    await press("delete-account-sheet-confirm");
    later();
    await press("delete-account-final-confirm");
    expect(screen.getByTestId("account-error")).toHaveTextContent("We couldn't confirm. Check your connection and try again.");
  });
});

describe("AccountActions, an access code", () => {
  test("keeps Sign out at once and Delete my data; no account buttons", async () => {
    await setToken("CODE", "tester", "code", null);
    await render(<AccountActions router={{ canDismiss: () => true, dismissAll: () => {} }} />);
    await settle();
    expect(screen.queryByTestId("account-delete")).toBeNull();
    expect(screen.queryByTestId("account-sign-out-all")).toBeNull();
    expect(screen.getByTestId("delete-my-data")).toBeTruthy();
    await press("sign-out");
    expect(mocks.reset).toHaveBeenCalledTimes(1);
    expect(mocks.signOut).not.toHaveBeenCalled();
  });
});
