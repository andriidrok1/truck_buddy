import { createOfflineHint, isNetworkError, OFFLINE_HINT_MS } from "../net/offlineHint";

const T0 = 1_700_000_000_000;

describe("isNetworkError", () => {
  test("fetch failures and the api wrapper's network errors count", () => {
    expect(isNetworkError(new TypeError("Network request failed"))).toBe(true);
    expect(isNetworkError(new TypeError("Failed to fetch"))).toBe(true);
    expect(isNetworkError(new Error("/route failed: network (unexpected text/html response)"))).toBe(true);
    expect(isNetworkError(new Error("timed out"))).toBe(true);
    expect(isNetworkError("ECONNREFUSED")).toBe(true);
  });

  test("HTTP statuses and a plain abort do not", () => {
    expect(isNetworkError(new Error("/route failed: 502"))).toBe(false);
    expect(isNetworkError(new Error("/agent failed: 422"))).toBe(false);
    expect(isNetworkError(Object.assign(new Error("Aborted"), { name: "AbortError" }))).toBe(false);
  });
});

describe("createOfflineHint", () => {
  test("a mark holds for the window, then expires", () => {
    const h = createOfflineHint();
    expect(h.active(T0)).toBe(false);
    h.mark(T0);
    expect(h.active(T0)).toBe(true);
    expect(h.active(T0 + OFFLINE_HINT_MS - 1)).toBe(true);
    expect(h.active(T0 + OFFLINE_HINT_MS)).toBe(false);
    expect(h.until()).toBe(T0 + OFFLINE_HINT_MS);
  });

  test("a later mark extends the window; a success clears it", () => {
    const h = createOfflineHint(20000);
    h.mark(T0);
    h.mark(T0 + 15000);
    expect(h.active(T0 + 30000)).toBe(true);
    h.clear();
    expect(h.active(T0 + 16000)).toBe(false);
    expect(h.until()).toBe(0);
  });

  test("onChange fires on mark and on a clear that changes something", () => {
    const onChange = jest.fn();
    const h = createOfflineHint(20000, onChange);
    h.clear();
    expect(onChange).not.toHaveBeenCalled();
    h.mark(T0);
    h.clear();
    h.clear();
    expect(onChange).toHaveBeenCalledTimes(2);
  });
});
