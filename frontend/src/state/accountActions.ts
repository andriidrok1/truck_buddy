// What the account part of the truck screen does (src/components/AccountActions.tsx): sign out of this phone, sign
// out of every phone, delete the account. An access-code sign-in keeps its own "Sign out" and "Delete my data"
// (src/state/firstLaunch.ts); none of these is offered to it.

import { accountStillThere, api, ApiError, logout, type AccountDeleted } from "../api";
import { getToken, signedInWithAccount } from "../auth";
import { after401 } from "../net/tokenRenewal";
import { serverThenReset, signedOutReset, type Nav } from "./firstLaunch";

/** How long "Sign out" waits for the server to close this phone's session before signing out anyway. */
export const LOGOUT_WAIT_MS = 3000;

/**
 * "Sign out": this phone's session closes on the server, then the phone forgets the driver (signedOutReset) and the
 * root layout shows the sign-in screen. Best effort on the server side: offline, or with no answer within
 * LOGOUT_WAIT_MS, the phone signs out all the same and the session ends when it goes idle. The request has read the
 * token before the reset clears it, so a late answer still closes the right session.
 */
export async function signOut(close: () => Promise<boolean> = logout): Promise<void> {
  const closing = close().catch(() => false);
  let timer: ReturnType<typeof setTimeout> | undefined;
  await Promise.race([closing, new Promise<void>((r) => (timer = setTimeout(r, LOGOUT_WAIT_MS)))]);
  clearTimeout(timer);
  await signedOutReset();
}

/**
 * "Sign out of all phones": every session of the account closes on the server, this phone's included, and this
 * phone forgets the driver. False when the server could not be reached: the phone stays signed in, because
 * signing out here alone would leave a lost phone signed in while the driver thinks it is not.
 */
export async function signOutEverywhere(call: () => Promise<unknown> = api.logoutAll): Promise<boolean> {
  try {
    await call();
  } catch {
    return false;
  }
  await signedOutReset();
  return true;
}

export type DeleteOutcome =
  | { ok: true; appleRevoked: boolean | null }
  /** 409: the server will not delete this account (the only owner of a fleet that still has drivers). */
  | { ok: false; reason: "refused"; detail: string }
  /** 503, or no answer and the check said the account is still there: nothing was removed, and the server takes
   *  the same request again. */
  | { ok: false; reason: "failed" }
  /** No answer, and the check got none either: nothing on the phone was touched, the server state is unknown. */
  | { ok: false; reason: "unconfirmed" };

/** No answer to the DELETE: the server may have finished all the same (a timeout, a dead zone, the edge's 502/504).
 *  A 503 is the backend saying it did not. */
const noAnswer = (e: unknown) => !(e instanceof ApiError) || e.status === 502 || e.status === 504;

class Unconfirmed extends Error {}

/**
 * "Delete account and data": DELETE /api/account with every other request held (serverThenReset), then the phone
 * back to a fresh install (the language screen). On a 409 or any failure nothing on the phone changes. When the
 * DELETE gets no answer, GET /api/auth/check with the same token tells a deletion that went through (401 "no longer
 * registered": the phone resets as on a 200) from one that did not (200) and from no answer at all. A 401 (the
 * access token expired while the screen was open) is renewed outside the hold and the delete is sent once more.
 */
export async function deleteAccount(router: Nav, call: () => Promise<AccountDeleted> = api.deleteAccount): Promise<DeleteOutcome> {
  for (let attempt = 0; ; attempt++) {
    const sent = getToken();
    // With no answer, one cheap authenticated call says what happened, still under the hold, so no other request
    // gets the 401 first and signs the phone out with "You were signed out" instead of the fresh-install reset.
    // The DELETE's header is read in the same tick as this function is called (serverThenReset relies on it).
    const checked = async (): Promise<AccountDeleted> => {
      try {
        return await call();
      } catch (e) {
        if (!sent || !noAnswer(e)) throw e;
        const state = await accountStillThere(sent);
        if (state === "gone") return { ok: true, apple_revoked: null }; // what Apple said is not known here
        throw state === "alive" ? e : new Unconfirmed();
      }
    };
    const r = await serverThenReset(router, checked);
    if (r.ok) {
      const appleRevoked = r.value?.apple_revoked ?? null;
      // Apple's settings keep listing the app until the driver removes it there; nothing to show on a fresh phone.
      if (appleRevoked === false) console.warn("[account] deleted, but Apple did not take the revocation");
      return { ok: true, appleRevoked };
    }
    const e = r.error;
    if (e instanceof Unconfirmed) return { ok: false, reason: "unconfirmed" };
    if (e instanceof ApiError && e.status === 409) return { ok: false, reason: "refused", detail: e.detail ?? REFUSED };
    // Our 401 carries a detail; a proxy's does not and says nothing about the token.
    if (e instanceof ApiError && e.status === 401 && e.detail !== null && attempt === 0 && sent && (await after401(sent))) continue;
    return { ok: false, reason: "failed" };
  }
}

/** "Clear memory" on the history screen. An account clears the conversations and what the copilot learned from them
 *  (DELETE /api/memory/history); the truck profile and the trips stay. An access-code sign-in has only the older
 *  full wipe (DELETE /api/memory), as before. */
export const clearConversations = (): Promise<unknown> => (signedInWithAccount() ? api.clearHistory() : api.clearMemory());

const REFUSED = "This account can't be deleted from the app. Write to support@truckbuddy.io.";
