import { fireEvent, render, screen } from "@testing-library/react-native";
import { Linking } from "react-native";

import { ReviewsGist, reviewsGistLine } from "../components/ReviewsGist";
import type { Review } from "../types";

// The icon font needs a native module; a text stand-in keeps the test about the reviews.
jest.mock("../components/Icon", () => {
  const { Text } = jest.requireActual<typeof import("react-native")>("react-native");
  return { Icon: ({ name }: { name: string }) => <Text>{`icon:${name}`}</Text> };
});

const reviews: Review[] = [
  { author: "Ray D.", rating: 5, text: "Plenty of truck parking out back, clean showers.", uri: "https://maps.google.com/?cid=1#review1" },
  { author: "Maria", rating: 3, text: "Diesel pumps slow at night.", uri: "https://maps.google.com/?cid=1#review2" },
  { author: "Third", rating: 4, text: "Not shown, only two fit.", uri: "https://maps.google.com/?cid=1#review3" },
];

// RNTL 14: render() and fireEvent.* are async.
describe("ReviewsGist", () => {
  const openURL = jest.spyOn(Linking, "openURL").mockResolvedValue(undefined);
  beforeEach(() => openURL.mockClear());

  test("shows at most two reviews with their authors and the Google attribution", async () => {
    await render(<ReviewsGist reviews={reviews} />);
    expect(screen.getByText("Ray D.")).toBeTruthy();
    expect(screen.getByText("Maria")).toBeTruthy();
    expect(screen.queryByText("Third")).toBeNull();
    expect(screen.getByText("Reviews from Google")).toBeTruthy();
  });

  test("tapping a review opens its Google link", async () => {
    await render(<ReviewsGist reviews={reviews} />);
    await fireEvent.press(screen.getByTestId("review-1"));
    expect(openURL).toHaveBeenCalledWith("https://maps.google.com/?cid=1#review2");
  });

  test("'More' opens the first review's link", async () => {
    await render(<ReviewsGist reviews={reviews} />);
    await fireEvent.press(screen.getByTestId("reviews-more"));
    expect(openURL).toHaveBeenCalledWith("https://maps.google.com/?cid=1#review1");
  });

  test("a review without a link neither opens anything nor throws", async () => {
    await render(<ReviewsGist reviews={[{ author: "Anon", text: "ok", uri: null }]} />);
    await fireEvent.press(screen.getByTestId("review-0"));
    expect(openURL).not.toHaveBeenCalled();
    expect(screen.queryByTestId("reviews-more")).toBeNull();
  });

  test("renders nothing for empty text", async () => {
    await render(<ReviewsGist reviews={[{ author: "Anon", text: "  " }]} />);
    expect(screen.queryByTestId("reviews-gist")).toBeNull();
  });
});

describe("reviewsGistLine", () => {
  test("star rating plus the first review in quotes", () => {
    expect(reviewsGistLine(reviews, 4.12)).toBe("★ 4.1 · “Plenty of truck parking out back, clean showers.”");
  });
  test("no rating: just the quote", () => {
    expect(reviewsGistLine(reviews, null)).toBe("“Plenty of truck parking out back, clean showers.”");
  });
  test("nothing to say", () => {
    expect(reviewsGistLine(undefined, 4)).toBeNull();
    expect(reviewsGistLine([], 4)).toBeNull();
  });
});
