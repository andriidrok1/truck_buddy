// "Welcome back" (Figma f5Uha6gopQHRmcGlMXdCD9, 135:2, screen 4): what came back with the account, shown once.
import AsyncStorage from "@react-native-async-storage/async-storage";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react-native";

import { ApiError, api } from "../api";
import { beginRestore, resetProfile, restorePending, saveProfile } from "../data/profile";
import { queryClient } from "../query-client";
import "../state/afterSignIn"; // registers what happens when the profile lands
import { prefsSummary, WelcomeBack } from "../components/WelcomeBack";
import { DEFAULT_TRUCK, forgetTruckProfile, isOnboarded } from "../hooks/useTruckProfile";
import { t, useLangStore } from "../i18n";
import { resetAccountForTests, restoreBypassed, saveAccount, setWelcomeBack, useWelcomeBack, welcomeBackDue } from "../state/account";
import { rootScreen } from "../state/firstLaunch";

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
jest.mock("../api", () => {
  const actual = jest.requireActual("../api");
  return { ...actual, api: { ...actual.api, profile: jest.fn(), saveProfile: jest.fn() } };
});
const A = api as unknown as { profile: jest.Mock };

let client: QueryClient;
const settle = async () => {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
};
/** `landed`: wait for the truck card (react-query hands the answer over on a timer of its own). */
async function show(landed = true) {
  await render(
    <QueryClientProvider client={client}>
      <WelcomeBack />
    </QueryClientProvider>,
  );
  await settle();
  if (landed) await screen.findByTestId("welcome-back-card");
}
const enabled = () => screen.getByTestId("welcome-back-open-map").props.accessibilityState?.disabled !== true;
/** A returning driver has just signed in: the profile is awaited. */
async function returning() {
  setWelcomeBack(true);
  await beginRestore();
}

beforeEach(async () => {
  await AsyncStorage.clear();
  resetAccountForTests();
  await resetProfile();
  await forgetTruckProfile();
  useLangStore.setState({ lang: "en", chosen: true, loaded: true });
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0, retryDelay: 1 } } });
  A.profile.mockReset();
});
afterEach(() => {
  client.clear();
  queryClient.clear(); // the app's own client (resetProfile writes to it): its gc timer would keep jest alive
});

const SERVER_TRUCK = { ...DEFAULT_TRUCK, height_in: 168, trailer_ft: 48, weight_lb: 74000, driver_name: "Mike", avoid_tolls: true, favorite_chains: ["Love's", "Pilot"], break_hours: 4 };

test("the name the driver goes by, the truck from the server in the app's own format, the preferences, one button", async () => {
  await saveAccount({ name: "Michael Johnson", email: "m@example.com", provider: "apple" });
  await returning();
  A.profile.mockResolvedValue({ profile: SERVER_TRUCK, source: "driver" });
  await show();
  expect(screen.getByText("Welcome back, Mike")).toBeTruthy(); // what he asked to be called, not the account's "Michael"
  expect(screen.getByText("Here is what I brought over.")).toBeTruthy();
  expect(screen.getByTestId("welcome-back-truck").props.children).toBe(`14'0" · 48' · 74k lb`);
  expect(screen.getByTestId("welcome-back-prefs").props.children).toBe("Avoid toll roads · Love's, Pilot · Every 4 h");
  expect(screen.getByText("Open the map")).toBeTruthy();
  expect(enabled()).toBe(true);
  expect(screen.queryByTestId("welcome-back-loading")).toBeNull();
  expect(await isOnboarded()).toBe(true); // written when the profile landed
});

test("no preferences set: the card has the truck only; no name on the profile: the account's first name", async () => {
  await saveAccount({ name: "Olena Kovalenko", email: null, provider: "google" });
  await returning();
  A.profile.mockResolvedValue({ profile: { ...DEFAULT_TRUCK, height_in: 150 }, source: "driver" });
  await show();
  expect(screen.getByText("Welcome back, Olena")).toBeTruthy();
  expect(screen.getByTestId("welcome-back-truck").props.children).toBe(`12'6" · 53' · 80k lb`);
  expect(screen.queryByTestId("welcome-back-prefs")).toBeNull();
  expect(prefsSummary(DEFAULT_TRUCK)).toEqual([]);
});

test("the truck is on its way: a spinner, no numbers, and Open the map does nothing yet", async () => {
  let land: (v: unknown) => void = () => {};
  A.profile.mockReturnValue(new Promise((resolve) => (land = resolve)));
  await returning();
  await show(false);
  expect(screen.getByText("Welcome back")).toBeTruthy(); // nobody to name yet
  expect(screen.getByTestId("welcome-back-loading")).toBeTruthy();
  expect(screen.queryByTestId("welcome-back-card")).toBeNull(); // never the phone's default truck
  expect(screen.queryByTestId("welcome-back-truck")).toBeNull();
  expect(enabled()).toBe(false);
  await fireEvent.press(screen.getByTestId("welcome-back-open-map"));
  expect(welcomeBackDue()).toBe(true);

  await act(async () => {
    land({ profile: SERVER_TRUCK, source: "driver" });
    await new Promise((r) => setTimeout(r, 0));
  });
  await screen.findByTestId("welcome-back-card");
  expect(enabled()).toBe(true);
});

test("the truck could not be fetched: the screen says so and offers Try again instead of letting the driver through", async () => {
  A.profile.mockRejectedValue(new ApiError(503, "/me/profile"));
  await returning();
  await show(false);
  await screen.findByTestId("welcome-back-retry");
  expect(screen.getByTestId("welcome-back-error").props.children).toBe("Couldn't load your truck. Check the connection and try again.");
  expect(screen.queryByTestId("welcome-back-open-map")).toBeNull();
  expect(screen.queryByTestId("welcome-back-card")).toBeNull();
  expect(screen.queryByText("Here is what I brought over.")).toBeNull();
  expect(welcomeBackDue()).toBe(true);
  expect(await isOnboarded()).toBe(false);

  A.profile.mockResolvedValue({ profile: SERVER_TRUCK, source: "driver" });
  await fireEvent.press(screen.getByTestId("welcome-back-retry"));
  await screen.findByTestId("welcome-back-card");
  expect(enabled()).toBe(true);
  expect(screen.queryByTestId("welcome-back-error")).toBeNull();
});

test("the pull keeps failing: after the first failure the driver can go to the map anyway; the restore stays pending and nothing is pushed", async () => {
  A.profile.mockRejectedValue(new ApiError(503, "/me/profile"));
  await returning();
  await show(false);
  await screen.findByTestId("welcome-back-skip");
  expect(screen.getByText("Open the map anyway")).toBeTruthy();
  expect(screen.getByTestId("welcome-back-retry")).toBeTruthy(); // still the primary action
  await fireEvent.press(screen.getByTestId("welcome-back-skip"));
  expect(welcomeBackDue()).toBe(false);
  expect(restoreBypassed()).toBe(true);
  expect(restorePending()).toBe(true); // the map opens, the truck is still awaited
  // What the root layout shows: Welcome back held while restoring, unless bypassed.
  expect(welcomeBackDue() || (restorePending() && !restoreBypassed())).toBe(false);
  const push = (api as unknown as { saveProfile: jest.Mock }).saveProfile;
  push.mockReset();
  await saveProfile({ ...DEFAULT_TRUCK, avoid_tolls: true }); // a setting on the truck screen
  expect(push).not.toHaveBeenCalled();
  expect(await isOnboarded()).toBe(false);
  // A later sign-in brings Welcome back again.
  setWelcomeBack(true);
  expect(restoreBypassed()).toBe(false);
});

test("while it is still loading there is no way around it (the first attempt has not failed yet)", async () => {
  A.profile.mockReturnValue(new Promise(() => {}));
  await returning();
  await show(false);
  expect(screen.queryByTestId("welcome-back-skip")).toBeNull();
});

test("the server has no profile for this 'returning' account (404): no Welcome back, the root moves on and onboarding is ahead", async () => {
  A.profile.mockRejectedValue(new ApiError(404, "/me/profile"));
  await returning();
  const due = await renderHook(() => useWelcomeBack());
  await show(false);
  await waitFor(() => expect(welcomeBackDue()).toBe(false));
  await due.rerender({});
  expect(rootScreen(true, true, "yes", "in", due.result.current)).toBe("app");
  expect(await isOnboarded()).toBe(false);
  expect(screen.queryByTestId("welcome-back-card")).toBeNull();
});

test("shown once: Open the map takes the root to the app, and nothing brings the screen back", async () => {
  A.profile.mockResolvedValue({ profile: SERVER_TRUCK, source: "driver" });
  await returning();
  const due = await renderHook(() => useWelcomeBack());
  expect(rootScreen(true, true, "yes", "in", due.result.current)).toBe("welcome");
  await show();
  await fireEvent.press(screen.getByTestId("welcome-back-open-map"));
  await due.rerender({});
  expect(due.result.current).toBe(false);
  expect(rootScreen(true, true, "yes", "in", due.result.current)).toBe("app");
  // A cold start later: the flag was never on disk, and the restore is over.
  resetAccountForTests();
  expect(welcomeBackDue()).toBe(false);
  expect((await AsyncStorage.getAllKeys()).some((k) => /welcome|restore/i.test(k))).toBe(false);
});

test("in Russian", async () => {
  useLangStore.setState({ lang: "ru" });
  A.profile.mockResolvedValue({ profile: { ...SERVER_TRUCK, favorite_chains: [], break_hours: null }, source: "driver" });
  await returning();
  await show();
  expect(screen.getByText("С возвращением, Mike")).toBeTruthy();
  expect(screen.getByText("Вот что уже на месте.")).toBeTruthy();
  expect(screen.getByText("Открыть карту")).toBeTruthy();
  expect(screen.getByText("Трак")).toBeTruthy();
  expect(screen.getByText("Предпочтения")).toBeTruthy();
  expect(screen.getByText("Объезжать платные дороги")).toBeTruthy();
  expect(screen.getByTestId("welcome-back-open-map").props.accessibilityLabel).toBe("Открыть карту");
  expect(t("Open the map anyway", undefined, "ru")).toBe("Всё равно открыть карту");
});
