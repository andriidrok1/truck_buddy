import { fireEvent, render, screen } from "@testing-library/react-native";
import { StyleSheet } from "react-native";

import { OptionsList } from "../components/OptionsList";
import { themes } from "../theme";
import type { Place } from "../types";

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
jest.mock("../components/ParkingStatus", () => ({ ParkingStatus: () => null }));

const place = (id: string, name: string): Place =>
  ({ id, name, category: "truck_stop", lat: 38.6, lng: -121.5, tags: [], distance_mi: 3.4, rating: null }) as unknown as Place;
const inner = (id: string) => StyleSheet.flatten((screen.getByTestId(id).children[0] as unknown as { props: { style: never } }).props.style);

// UI review 2026-11-05 (ui-options-light.png): one filled Route in the list, 56 pt buttons, the card routes too.
describe("OptionsList", () => {
  const options = [place("a", "Sacramento 49er Travel Plaza"), place("b", "Fuel Stop"), place("c", "West Sac Truck Stop")];

  test("only the first card has the filled Route button; all buttons are 56 pt", async () => {
    await render(<OptionsList options={options} parking={{}} onParking={() => {}} onRoute={() => {}} />);
    expect(screen.getByText("3 places nearby")).toBeTruthy();
    expect(inner("option-route-button-a").backgroundColor).toBe(themes.light.brandPrimary);
    expect(inner("option-route-button-b").backgroundColor).not.toBe(themes.light.brandPrimary);
    for (const id of ["option-route-button-a", "option-route-button-c", "option-parking-button-b"]) expect(inner(id).height).toBe(56);
    expect(screen.queryByText("No rating yet")).toBeNull();
  });

  test("a tap on the card routes there", async () => {
    const onRoute = jest.fn();
    await render(<OptionsList options={options} parking={{}} onParking={() => {}} onRoute={onRoute} />);
    await fireEvent.press(screen.getByTestId("option-card-b"));
    expect(onRoute).toHaveBeenCalledWith(options[1]);
  });
});
