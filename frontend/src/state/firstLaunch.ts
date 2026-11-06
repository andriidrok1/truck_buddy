// "Delete my data" on the truck screen: after the server copy is gone, the phone goes back to how a fresh install
// looks. Every key the app writes is removed and every store that read one is reset, so the root layout shows the
// language screen, then consent, a new anonymous sign-in, and the map sends the driver through onboarding again
// (QA 2026-11-05, M-1: language, theme, consent and onboarding stayed, and the code screen came up instead).
//
// Not removed: the storage schema stamp (a fresh install writes it on its first start too) and downloaded offline
// map packs (tiles, nothing about the driver).

import { api } from "../api";
import { forgetIdentity } from "../auth";
import { forgetConsent } from "../consent";
import { resetProfile } from "../data/profile";
import { setEldToken } from "../eld";
import { forgetArrived } from "../geo/arrival";
import { forgetTruckProfile } from "../hooks/useTruckProfile";
import { forgetLang } from "../i18n";
import { tripLog } from "../net/tripEvents";
import { queryClient } from "../query-client";
import { setSim } from "../sim";
import { forgetThemePref } from "../theme";
import { forgetHeight } from "../truck/heightConfirmed";
import { clearSavedRoute } from "./routeStore";
import { forgetSettings } from "./settingsStore";
import { useTripStore } from "./tripStore";

export async function resetToFirstLaunch(): Promise<void> {
  useTripStore.getState().endRoute();
  setSim(null);
  await resetProfile(); // the default truck in memory and in the query cache, no pending server sync
  queryClient.clear(); // conversation memory, the server profile: fetched again for the new identity
  await Promise.all([
    forgetTruckProfile(),
    forgetHeight(),
    forgetSettings(),
    clearSavedRoute(),
    forgetArrived(),
    tripLog.clear(),
    setEldToken(null),
    forgetThemePref(),
  ]);
  // The gates last, language first: once `chosen` is false the layout shows the language screen, so the token
  // removal below cannot start an automatic sign-in behind it.
  await forgetLang();
  await forgetConsent();
  await forgetIdentity();
}

/** Which screen the root layout shows (app/_layout.tsx): the gates in first-launch order, then the app's stack. */
export type RootScreen = "language" | "consent" | "device" | "app";
export function rootScreen(langChosen: boolean, consent: "loading" | "yes" | "no", auth: "loading" | "in" | "out"): RootScreen {
  if (!langChosen) return "language";
  if (consent !== "yes") return "consent";
  if (auth !== "in") return "device";
  return "app";
}

type Nav = { canDismiss: () => boolean; dismissAll: () => void };

/**
 * The whole "Delete my data": the server records first (while the token is still valid), then off the truck screen,
 * so the stack that comes back after the gates starts on the map (which sends a driver who is not onboarded to
 * onboarding), then the fresh-install state. False when the server could not be reached: nothing is touched then.
 */
export async function deleteMyData(router: Nav, clearServer: () => Promise<unknown> = api.clearMemory): Promise<boolean> {
  try {
    await clearServer();
  } catch {
    return false;
  }
  if (router.canDismiss()) router.dismissAll();
  await resetToFirstLaunch();
  return true;
}
