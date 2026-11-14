import { fireEvent, render, screen } from "@testing-library/react-native";

import { ProvidersList } from "../components/ProvidersList";
import type { Provider, ProvidersResult } from "../types";

jest.mock("react-native-reanimated", () => {
  const { View } = jest.requireActual<typeof import("react-native")>("react-native");
  const anim = { delay: () => anim, duration: () => anim };
  return {
    __esModule: true,
    default: { View },
    FadeInDown: anim,
    useSharedValue: (v: unknown) => {
      const sv = { value: v, get: () => sv.value, set: (n: unknown) => void (sv.value = n) };
      return sv;
    },
    useAnimatedStyle: (f: () => object) => f(),
    withSpring: (v: unknown) => v,
  };
});
jest.mock("../components/Icon", () => {
  const { Text } = jest.requireActual<typeof import("react-native")>("react-native");
  return { Icon: ({ name }: { name: string }) => <Text>{`icon:${name}`}</Text> };
});

const provider = (id: string, extra: Partial<Provider>): Provider => ({
  id, name: id, lat: 38.6, lng: -121.5, rating_count: 0, listed_24_7: false, shop_only: false, score: 1, source: "google",
  facts: { mobile_service: null, hours_24_7: null, truck_focused: false, prices: [], payment: [], red_flags: [], praise: [] },
  quotes: [], ...extra,
} as Provider);

const data: ProvidersResult = {
  providers: [
    provider("unknown", { phone: "+19165550100" }), // the backend's best match, distance not known
    provider("far", { distance_mi: 12, phone: "+19165550101" }),
    provider("near", { distance_mi: 3 }), // no phone number
  ],
};
const names = () => screen.getAllByTestId(/^provider-name-/).map((n) => n.props.children);

describe("ProvidersList", () => {
  test("Nearest puts a provider with no known distance last, not first", async () => {
    await render(<ProvidersList data={data} onRoute={() => {}} />);
    expect(names()).toEqual(["unknown", "far", "near"]);
    await fireEvent.press(screen.getByTestId("provider-sort-nearest"));
    expect(names()).toEqual(["near", "far", "unknown"]);
  });

  test("no phone number: no Call button, Route is still there", async () => {
    await render(<ProvidersList data={data} onRoute={() => {}} />);
    expect(screen.getByTestId("provider-call-button-far")).toBeTruthy();
    expect(screen.queryByTestId("provider-call-button-near")).toBeNull();
    expect(screen.getByTestId("provider-route-button-near")).toBeTruthy();
  });
});
