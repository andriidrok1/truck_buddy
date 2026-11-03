import { __resetOnline, getOnline, isOnline, selectOnline, startNet, useNetStore } from "../state/netStore";
import { offlineHint } from "../net/offlineHint";

jest.mock("@react-native-community/netinfo", () =>
  // eslint-disable-next-line @typescript-eslint/no-require-imports -- jest's own mock file, CommonJS
  require("@react-native-community/netinfo/jest/netinfo-mock"),
);

const T0 = 1_700_000_000_000;

beforeEach(() => __resetOnline());

test("unknown before any event; a failed call flips the selector to offline and back when the window ends", () => {
  jest.useFakeTimers();
  startNet();
  expect(getOnline(T0)).toBeNull();
  expect(isOnline()).toBe(true);
  expect(selectOnline(useNetStore.getState())).toBeNull();
  offlineHint.mark(Date.now());
  expect(selectOnline(useNetStore.getState())).toBe(false);
  jest.advanceTimersByTime(20001 + 5);
  expect(selectOnline(useNetStore.getState())).toBeNull();
  jest.useRealTimers();
});

test("NetInfo's verdict lands in the store", () => {
  useNetStore.setState({ net: false });
  expect(getOnline()).toBe(false);
  expect(isOnline()).toBe(false);
  useNetStore.setState({ net: true });
  expect(isOnline()).toBe(true);
});
