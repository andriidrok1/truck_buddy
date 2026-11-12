// "Welcome back" (Figma f5Uha6gopQHRmcGlMXdCD9, 135:2, screen 4): what came back with the account, shown once.
import AsyncStorage from "@react-native-async-storage/async-storage";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react-native";

import { ApiError, api } from "../api";
import { prefsSummary, WelcomeBack } from "../components/WelcomeBack";
import { DEFAULT_TRUCK, forgetTruckProfile } from "../hooks/useTruckProfile";
import { useLangStore } from "../i18n";
import { resetAccountForTests, saveAccount, setWelcomeBack, useWelcomeBack, welcomeBackDue } from "../state/account";
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

beforeEach(async () => {
  await AsyncStorage.clear();
  resetAccountForTests();
  await forgetTruckProfile();
  useLangStore.setState({ lang: "en", chosen: true, loaded: true });
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  A.profile.mockReset();
});
afterEach(() => client.clear());

const SERVER_TRUCK = { ...DEFAULT_TRUCK, height_in: 168, trailer_ft: 48, weight_lb: 74000, driver_name: "Mike", avoid_tolls: true, favorite_chains: ["Love's", "Pilot"], break_hours: 4 };

test("the name the driver goes by, the truck from the server in the app's own format, the preferences, one button", async () => {
  await saveAccount({ name: "Michael Johnson", email: "m@example.com", provider: "apple" });
  A.profile.mockResolvedValue({ profile: SERVER_TRUCK, source: "driver" });
  await show();
  expect(screen.getByText("Welcome back, Mike")).toBeTruthy(); // what he asked to be called, not the account's "Michael"
  expect(screen.getByText("Here is what I brought over.")).toBeTruthy();
  expect(screen.getByTestId("welcome-back-truck").props.children).toBe(`14'0" · 48' · 74k lb`);
  expect(screen.getByTestId("welcome-back-prefs").props.children).toBe("Avoid toll roads · Love's, Pilot · Every 4 h");
  expect(screen.getByText("Open the map")).toBeTruthy();
  expect(screen.queryByTestId("welcome-back-loading")).toBeNull();
});

test("no preferences set: the card has the truck only; no name on the profile: the account's first name", async () => {
  await saveAccount({ name: "Olena Kovalenko", email: null, provider: "google" });
  A.profile.mockResolvedValue({ profile: { ...DEFAULT_TRUCK, height_in: 162 }, source: "driver" });
  await show();
  expect(screen.getByText("Welcome back, Olena")).toBeTruthy();
  expect(screen.getByTestId("welcome-back-truck")).toBeTruthy();
  expect(screen.queryByTestId("welcome-back-prefs")).toBeNull();
  expect(prefsSummary(DEFAULT_TRUCK)).toEqual([]);
});

test("the truck has not arrived yet: a spinner, no numbers; it could not be fetched: the screen claims nothing and the button still works", async () => {
  let fail: (e: unknown) => void = () => {};
  A.profile.mockReturnValue(new Promise((_, reject) => (fail = reject)));
  setWelcomeBack(true);
  await show(false);
  expect(screen.getByText("Welcome back")).toBeTruthy(); // nobody to name yet
  expect(screen.getByTestId("welcome-back-loading")).toBeTruthy();
  expect(screen.queryByTestId("welcome-back-card")).toBeNull();
  await act(async () => {
    fail(new ApiError(503, "/me/profile"));
    await new Promise((r) => setTimeout(r, 0));
  });
  await waitFor(() => expect(screen.queryByTestId("welcome-back-loading")).toBeNull());
  expect(screen.queryByTestId("welcome-back-card")).toBeNull();
  expect(screen.queryByText("Here is what I brought over.")).toBeNull();
  await fireEvent.press(screen.getByTestId("welcome-back-open-map"));
  expect(welcomeBackDue()).toBe(false);
});

test("shown once: Open the map takes the root to the app, and nothing brings the screen back", async () => {
  A.profile.mockResolvedValue({ profile: SERVER_TRUCK, source: "driver" });
  setWelcomeBack(true);
  const due = await renderHook(() => useWelcomeBack());
  expect(rootScreen(true, true, "yes", "in", due.result.current)).toBe("welcome");
  await show();
  await fireEvent.press(screen.getByTestId("welcome-back-open-map"));
  await due.rerender({});
  expect(due.result.current).toBe(false);
  expect(rootScreen(true, true, "yes", "in", due.result.current)).toBe("app");
  // A cold start later: the flag was never on disk.
  resetAccountForTests();
  expect(welcomeBackDue()).toBe(false);
  expect((await AsyncStorage.getAllKeys()).some((k) => /welcome/i.test(k))).toBe(false);
});

test("in Russian", async () => {
  useLangStore.setState({ lang: "ru" });
  A.profile.mockResolvedValue({ profile: { ...SERVER_TRUCK, favorite_chains: [], break_hours: null }, source: "driver" });
  await show();
  expect(screen.getByText("С возвращением, Mike")).toBeTruthy();
  expect(screen.getByText("Вот что уже на месте.")).toBeTruthy();
  expect(screen.getByText("Открыть карту")).toBeTruthy();
  expect(screen.getByText("Трак")).toBeTruthy();
  expect(screen.getByText("Предпочтения")).toBeTruthy();
  expect(screen.getByText("Объезжать платные дороги")).toBeTruthy();
  expect(screen.getByTestId("welcome-back-open-map").props.accessibilityLabel).toBe("Открыть карту");
});
