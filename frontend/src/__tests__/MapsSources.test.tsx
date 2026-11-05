import { fireEvent, render, screen } from "@testing-library/react-native";
import { Linking } from "react-native";

import { replyCaption } from "../dialog/caption";
import { MapsSources } from "../components/MapsSources";

const SRC = [
  { name: "Pilot Travel Center", url: "https://maps.google.com/?cid=1" },
  { name: "Love's Travel Stop", url: "https://maps.google.com/?cid=2" },
];

// RNTL 14: render() and fireEvent.* are async.
describe("MapsSources (Grounding with Google Maps attribution)", () => {
  test("shows 'Google Maps' and each place as a link that opens it", async () => {
    const open = jest.spyOn(Linking, "openURL").mockResolvedValue(undefined);
    await render(<MapsSources sources={SRC} />);
    expect(screen.getByText("Google Maps")).toBeTruthy();
    const links = screen.getAllByTestId("maps-source-link");
    expect(links).toHaveLength(2);
    await fireEvent.press(screen.getByText("Love's Travel Stop"));
    expect(open).toHaveBeenCalledWith("https://maps.google.com/?cid=2");
  });

  test("nothing without sources", async () => {
    await render(<MapsSources sources={[]} />);
    expect(screen.queryByTestId("maps-sources")).toBeNull();
  });

  test("the reply caption carries the sources only when there are some", () => {
    expect(replyCaption("Drivers say it fills up by 6.", undefined, 0, SRC).sources).toEqual(SRC);
    expect(replyCaption("Route set.", undefined, 0, [])).not.toHaveProperty("sources");
    expect(replyCaption("Route set.", undefined, 0)).not.toHaveProperty("sources");
  });
});
