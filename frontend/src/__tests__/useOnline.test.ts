import NetInfo from "@react-native-community/netinfo";

import { __resetOnline, fromNetInfo, getOnline, isOnline } from "../hooks/useOnline";
import { offlineHint } from "../net/offlineHint";

jest.mock("@react-native-community/netinfo", () =>
  // eslint-disable-next-line @typescript-eslint/no-require-imports -- jest's own mock file, CommonJS
  require("@react-native-community/netinfo/jest/netinfo-mock"),
);

const T0 = 1_700_000_000_000;

describe("useOnline module state", () => {
  beforeEach(() => __resetOnline());

  test("reachability wins over isConnected; null falls back to isConnected", () => {
    expect(fromNetInfo({ isConnected: true, isInternetReachable: false })).toBe(false);
    expect(fromNetInfo({ isConnected: true, isInternetReachable: null })).toBe(true);
    expect(fromNetInfo({ isConnected: false, isInternetReachable: null })).toBe(false);
    expect(fromNetInfo({ isConnected: null, isInternetReachable: null })).toBeNull();
  });

  test("before any event: unknown, and isOnline() does not block", () => {
    expect(getOnline(T0)).toBeNull();
    expect(isOnline()).toBe(true);
    expect(NetInfo.addEventListener).toBeDefined();
  });

  test("a failed API call marks the link offline for its window", () => {
    offlineHint.mark(T0);
    expect(getOnline(T0 + 1000)).toBe(false);
    expect(getOnline(T0 + 30000)).toBeNull();
    offlineHint.clear();
    expect(getOnline(T0 + 1000)).toBeNull();
  });
});
