// "Delete my data" on the truck screen: after the server copy is gone, the phone goes back to how a fresh install
// looks. Every key the app writes is removed and every store that read one is reset, so the root layout shows the
// language screen, the intro, then sign-in and consent, and the map sends the driver through onboarding again
// (QA 2026-11-05, M-1: language, theme, consent and onboarding stayed, and the code screen came up instead).
//
// Not removed: the storage schema stamp (a fresh install writes it on its first start too) and downloaded offline
// map packs (tiles, nothing about the driver).

import { useSyncExternalStore } from "react";

import { ApiError, wipeServerData } from "../api";
import { forgetIdentity, getToken, holdToken, identitySub, signedInVia, signedInViaKnown, type SignedInVia } from "../auth";
import { forgetConsent } from "../consent";
import { resetProfile } from "../data/profile";
import { setEldToken } from "../eld";
import { forgetArrived } from "../geo/arrival";
import { forgetTruckProfile } from "../hooks/useTruckProfile";
import { forgetLang } from "../i18n";
import { renewToken, setIdentityRejectedHandler } from "../net/tokenRenewal";
import { tripLog } from "../net/tripEvents";
import { queryClient } from "../query-client";
import { setSim } from "../sim";
import { forgetThemePref } from "../theme";
import { forgetHeight } from "../truck/heightConfirmed";
import { removeAllAppKeys, removeIdentityKeys } from "../utils/storage";
import { IDENTITY_KEYS, markWipePending } from "../utils/storage/pendingWipe";
import { forgetAccount, setSignInNotice } from "./account";
import { dropHeldTrip, holdTrip } from "./heldTrip";
import { forgetHowTo } from "./howTo";
import { forgetIntro } from "./intro";
import { clearSavedRoute } from "./routeStore";
import { forgetSettings } from "./settingsStore";
import { resetTripStore, useTripStore } from "./tripStore";

const step = async (f: () => unknown) => {
  try {
    await f();
  } catch {
    // the key pass that follows and the next start cover it
  }
};

/** Everything on the phone that is about the signed-in driver, in memory and on disk: the trip and its lists, the
 *  truck and the change still waiting for the server, what was fetched for them, the trip log, the settings, the
 *  Motive key, the height and how-to flags, the account. Every step runs even if one before it failed. */
async function clearDriverData(keepTripLog = false): Promise<void> {
  await step(resetTripStore);
  await step(() => setSim(null));
  await step(resetProfile); // the default truck in memory and in the query cache, no pending server sync or restore
  await step(() => queryClient.clear()); // conversation memory, the server profile: fetched again for the new identity
  await Promise.all(
    [forgetTruckProfile, forgetHeight, forgetHowTo, forgetSettings, clearSavedRoute, forgetArrived, () => (keepTripLog ? undefined : tripLog.clear()), () => setEldToken(null), forgetAccount].map(step),
  );
}

/** Every step runs even if one before it failed; the keys a failed step left behind go in the final pass, and
 *  what even that cannot remove is finished on the next cold start (WIPE_PENDING_KEY). Returns the leftovers. */
export async function resetToFirstLaunch(): Promise<string[]> {
  await markWipePending();
  await step(dropHeldTrip);
  await clearDriverData();
  setSignInNotice(null);
  await step(forgetThemePref);
  // The gates last, language first: once `chosen` is false the layout shows the language screen, whatever the
  // token removal below does to the rest.
  await step(forgetLang);
  await step(forgetIntro);
  await step(forgetConsent);
  await step(forgetIdentity);
  return removeAllAppKeys();
}

let signingOut: Promise<string[]> | null = null;
/** The sign-out under way came with a notice or a trip kept (the server turned the token down). */
let signingOutForced = false;

const TRIP_LOG_KEY = "trip_events_pending";

/**
 * The phone stops being this driver's without becoming a fresh install: "Sign out", and a token the server turned
 * down (the account was deleted from another phone, the session was closed). Everything resetToFirstLaunch clears
 * goes, except the language, the consent, the intro flag and the theme, which are choices about the phone. The
 * root layout then shows the sign-in screen, and whoever signs in next starts from nothing of the previous driver:
 * no truck pushed to their account, no trip events sent under their name, no route, no Motive key.
 *
 * `notice`: the sign-in screen says "You were signed out. Sign in again." (the driver did not ask for this).
 * `keepTripFor`: the forced path only. The account (token `sub`) that was signed in: when it was driving a route,
 * the route and the pending trip events are set aside for that same account to come back to (heldTrip.ts), so a
 * session closed mid-drive costs a sign-in, not the trip. The driver's own "Sign out" passes nothing and keeps nothing.
 * Returns the keys that would not go; the next cold start, or the next sign-in, finishes them (pendingWipe.ts).
 */
export function signedOutReset(opts: { notice?: boolean; keepTripFor?: string | null } = {}): Promise<string[]> {
  const explicit = !opts.notice && !opts.keepTripFor;
  if (signingOut && explicit && signingOutForced) {
    // The driver's own "Sign out" while a forced one runs (review of #173, L3): it waits for that one, then takes
    // back what only the forced path keeps. No "You were signed out", no route set aside, no trip events left.
    return signingOut.then(async (left) => {
      setSignInNotice(null);
      await step(dropHeldTrip);
      await step(() => tripLog.clear());
      return left;
    });
  }
  if (!signingOut) {
    signingOutForced = !explicit;
    signingOut = (async () => {
      // Before anything is cleared: the route on disk is the one being driven.
      const sub = opts.keepTripFor ?? null;
      let held = false;
      if (sub && useTripStore.getState().driving) held = await holdTrip(sub).catch(() => false);
      if (!held) await step(dropHeldTrip);
      const kept = held ? [TRIP_LOG_KEY] : [];
      await markWipePending(IDENTITY_KEYS.filter((k) => !kept.includes(k)));
      // No new batch goes out from here; what is queued is dropped below. Not awaited: the batch on the wire may be
      // the very request whose 401 started this sign-out, and it finishes only after this function does.
      void tripLog.pause();
      try {
        // The disk copy is the only one from here: memory would come back from disk once more on the next mount.
        if (held) await step(() => tripLog.stash());
        await clearDriverData(held);
        setSignInNotice(opts.notice ? "signed_out" : null);
        await step(forgetIdentity); // the app's screens unmount here: nothing of theirs logs or saves after this
        if (!held) await step(() => tripLog.clear()); // an event logged while the screens were going away
        return await removeIdentityKeys(kept);
      } finally {
        tripLog.resume();
      }
    })().finally(() => {
      signingOut = null;
    });
  }
  return signingOut;
}

// The server rejected the token for good (src/net/tokenRenewal.ts after401): the same reset, with the notice, and
// the trip kept for the same account when it was on the road.
setIdentityRejectedHandler(() => signedOutReset({ notice: true, keepTripFor: identitySub() }));

/**
 * Sign-in is required since 1.1.0. A phone that still holds the anonymous device token of build 1.0 (`auth_via`
 * "device") is not signed in to anything a driver could get back: it is treated as signed out, and the token and
 * what the phone kept under it are cleared like any other sign-out. An access-code sign-in (testers, the stand,
 * App Review) stays as it is.
 */
export const countsAsSignedIn = (via: SignedInVia, known = true): boolean => via !== "device" || !known;

/** What the root layout feeds rootScreen: a device token is "out". `known` false (the Keychain could not say how
 *  the phone signed in): the phone stays in, nothing is decided on a guess. */
export function effectiveAuth(auth: "loading" | "in" | "out", via: SignedInVia, known = true): "loading" | "in" | "out" {
  return auth === "in" && !countsAsSignedIn(via, known) ? "out" : auth;
}

/** Once the stored token is read (root layout), and again after a failed read of `auth_via` is retried: clears a
 *  build-1.0 device identity. Only when `auth_via` was read and says "device", or is absent and the token is a
 *  device token (no `sid`). An unreadable `auth_via` never wipes a phone. True when it did. */
export async function retireDeviceIdentity(): Promise<boolean> {
  if (!getToken() || !signedInViaKnown() || countsAsSignedIn(signedInVia())) return false;
  await signedOutReset();
  return true;
}

/** Which screen the root layout shows (app/_layout.tsx): the gates in first-launch order, then the app's stack. */
export type RootScreen = "language" | "intro" | "signin" | "consent" | "welcome" | "app";
/**
 * First launch: language, the intro, sign-in (required: there is no way past it without an account), consent,
 * then the app. `introSeen`: src/state/intro.ts. A driver who has already agreed to the consent screen is past
 * first launch and never gets the intro, whatever the flag says (an install from before the intro existed).
 * `welcomeBack`: a returning driver just signed in on this phone (src/state/account.ts); shown once, after consent.
 */
export function rootScreen(
  langChosen: boolean,
  introSeen: boolean,
  consent: "loading" | "yes" | "no",
  auth: "loading" | "in" | "out",
  welcomeBack = false,
): RootScreen {
  if (!langChosen) return "language";
  if (!introSeen && consent !== "yes") return "intro";
  if (auth !== "in") return "signin";
  if (consent !== "yes") return "consent";
  return welcomeBack ? "welcome" : "app";
}

export type Nav = { canDismiss: () => boolean; dismissAll: () => void };

// One server wipe at a time for the whole app, whichever screen or button started it (review of #173, M1): a second
// truck screen gets the wipe already under way. A wipe of the other kind ("Delete my data" while "Delete account"
// runs) gets WipeBusy, never the result of a wipe it did not ask for.
export type WipeKind = "data" | "account";
export type WipeResult<T> = { ok: true; value: T } | { ok: false; error: unknown };
/** Another kind of server wipe is under way: nothing was sent for this one. */
export class WipeBusy extends Error {}
let wiping: { kind: WipeKind; result: Promise<WipeResult<unknown>> } | null = null;
const wipeListeners = new Set<() => void>();
const wipeChanged = () => wipeListeners.forEach((l) => l());

/** True while a server wipe (serverThenReset) is under way. */
export const wipeRunning = (): boolean => wiping !== null;

/** The same, for a screen: the truck screen keeps its Back closed meanwhile. */
export function useWipeRunning(): boolean {
  return useSyncExternalStore(
    (l) => {
      wipeListeners.add(l);
      return () => wipeListeners.delete(l);
    },
    wipeRunning,
    wipeRunning,
  );
}

/**
 * A server wipe, then the phone back to a fresh install: "Delete my data" (an access-code sign-in) and "Delete
 * account and data" (src/state/accountActions.ts). The server records first, while the token is still valid, then
 * off the truck screen, so the stack that comes back after the gates starts on the map (which sends a driver who is
 * not onboarded to onboarding), then the fresh-install state. When the server call fails nothing is touched and the
 * error comes back to the caller.
 *
 * Single-flight: a call made while a wipe of the same `kind` runs does not send anything and gets that wipe's result
 * (its `value` is what the first caller's `clearServer` returned); one of the other kind gets WipeBusy.
 */
export function serverThenReset<T>(router: Nav, clearServer: () => Promise<T>, kind: WipeKind): Promise<WipeResult<T>> {
  if (wiping && wiping.kind !== kind) return Promise.resolve({ ok: false, error: new WipeBusy() });
  if (!wiping) {
    const result = wipeOnce(router, clearServer).finally(() => {
      wiping = null;
      wipeChanged();
    });
    wiping = { kind, result };
    wipeChanged();
  }
  return wiping.result as Promise<WipeResult<T>>;
}

async function wipeOnce<T>(router: Nav, clearServer: () => Promise<T>): Promise<WipeResult<T>> {
  // Nothing may reach the server with the old token once the delete is under way: an event posted after it would
  // bring trip rows back for the driver just wiped (review of #129). The trip log stops sending (a batch already on
  // the wire lands first, before the DELETE), the DELETE takes the token with it, and every other request waits for
  // the DELETE to settle: on failure they go out with the same token as before, on success after the wipe, without
  // one. None goes out tokenless meanwhile, so no 401 can sign the phone out behind the dialog (re-review of #129).
  await tripLog.pause();
  let release: () => void = () => {};
  let value!: T;
  const underHold = async () => {
    const call = clearServer(); // reads authHeader() synchronously, before its first await
    release = holdToken();
    value = await call;
  };
  try {
    try {
      await underHold();
    } catch (e) {
      // The access token expired (or was refused): the renewal cannot run under the hold, which it would wait on.
      // So the hold goes, the token is renewed once, and the delete is sent once more with the new one. This is the
      // only retry of a wipe: "Delete account" relies on it and has none of its own.
      if (!(e instanceof ApiError) || e.status !== 401) throw e;
      release();
      if ((await renewToken(true)) !== "renewed") throw e;
      await underHold();
    }
  } catch (error) {
    release();
    tripLog.resume();
    return { ok: false, error };
  }
  try {
    if (router.canDismiss()) router.dismissAll();
    await resetToFirstLaunch();
  } finally {
    // Whatever threw above (review of #173, L-1): the held requests and the trip log never stay stopped.
    release(); // the queued requests go now, without the token
    tripLog.resume(); // empty now; the next events belong to the new identity
  }
  return { ok: true, value };
}

/** How "Delete my data" ended. "done": the phone is a fresh install. "failed": the server could not be reached or
 *  answered with an error; nothing on the phone was touched. "busy": "Delete account" is under way. */
export type DataOutcome = "done" | "failed" | "busy";

/** "Delete my data" for an access-code sign-in (DELETE /api/memory). */
export async function deleteMyData(router: Nav, clearServer: () => Promise<unknown> = wipeServerData): Promise<DataOutcome> {
  const r = await serverThenReset(router, clearServer, "data");
  if (r.ok) return "done";
  return r.error instanceof WipeBusy ? "busy" : "failed";
}
