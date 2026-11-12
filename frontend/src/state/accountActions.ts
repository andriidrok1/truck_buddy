// What the account part of the truck screen does (src/components/AccountActions.tsx): sign out of this phone, sign
// out of every phone, delete the account. An access-code sign-in keeps its own "Sign out" and "Delete my data"
// (src/state/firstLaunch.ts); none of these is offered to it.

import { api, ApiError, isNoAnswer, isNoLongerRegistered, logout, tokenState, type AccountDeleted, type RefusalCode } from "../api";
import { getToken, signedInWithAccount } from "../auth";
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

/** "done": every session closed and this phone signed out. "failed": the server answered and every phone is still
 *  signed in. "unconfirmed": no answer, and the check got none either; this phone stays signed in. */
export type EverywhereOutcome = "done" | "failed" | "unconfirmed";

/**
 * "Sign out of all phones": every session of the account closes on the server, this phone's included, and this
 * phone forgets the driver. When the server cannot be reached the phone stays signed in, because signing out here
 * alone would leave a lost phone signed in while the driver thinks it is not.
 *
 * No answer (review of #173, M2): the request may have gone through. GET /api/auth/check says which, with the token
 * the phone holds after the failure: request() renews an expired token on the way, and the old one would only say
 * "expired" (re-review, M-2). A 401 that says this session is over (`session_closed`, or a server without codes
 * saying "no longer registered", or `account_gone`) signs this phone out as on a 200 (the driver asked for it: no
 * notice, no route kept). A 200 means nothing closed. Any other 401, or no answer, is "unconfirmed".
 */
export async function signOutEverywhere(call: () => Promise<unknown> = api.logoutAll): Promise<EverywhereOutcome> {
  try {
    await call();
  } catch (e) {
    const now = getToken(); // after the failure: the one request() may have renewed
    if (!now) return "done"; // our 401 on the request itself, and the renewal signed the phone out already
    if (!isNoAnswer(e)) return "failed";
    const state = await tokenState(now);
    if (state === "alive") return "failed";
    if (state === "refused" || state === "unknown") return "unconfirmed";
  }
  await signedOutReset();
  return "done";
}

export type DeleteOutcome =
  | { ok: true; appleRevoked: boolean | null }
  /** 409: the server will not delete this account. `message` is the English text to show (translated by <Text>),
   *  picked by the 409's `code`, never by reading its `detail`. */
  | { ok: false; reason: "refused"; code: RefusalCode | null; message: string }
  /** 503 with our JSON `detail`: the backend says it removed nothing, and takes the same request again. The only
   *  outcome that may tell the driver "Nothing was removed". */
  | { ok: false; reason: "not_removed" }
  /** Any other answer from the server (a 500, a 4xx, a 401 the renewal could not fix): it did not delete, but did
   *  not say what it kept either. */
  | { ok: false; reason: "failed" }
  /** No answer to the DELETE, and the check could not settle it (the account still answers, which may only mean the
   *  deletion is still running, or the check got no answer either). Nothing on the phone was touched. */
  | { ok: false; reason: "unconfirmed" };

class Unconfirmed extends Error {}

/**
 * Treated as a deletion that went through, and the phone resets to a fresh install. Not proof that this request
 * deleted the account: our 401 "no longer registered" is also what a phone hears when the account was deleted
 * from another phone. Either way there is no account behind the token any more, so nothing on this phone or in
 * the copy ever says "your account was deleted" (review of #173, M3): the driver just sees the first launch again.
 */
const GONE: AccountDeleted = { ok: true, apple_revoked: null, deleted: 0, trip_events: 0, profile: false, sessions: 0 }; // counts and Apple's answer are not known here

/**
 * "Delete account and data": DELETE /api/account with every other request held (serverThenReset), then the phone
 * back to a fresh install (the language screen). On a 409 or any failure nothing on the phone changes.
 *
 * - A 401 "no longer registered" counts as a 200 (a retry after a deletion that went through, review of #173 H1).
 * - Any other 401 (the access token expired while the screen was open) goes to serverThenReset, which renews the
 *   token outside the hold and sends the delete once more. That is the only retry; there is none here.
 * - No answer: GET /api/auth/check with the same token, still under the hold, so no other request gets the 401
 *   first and signs the phone out with "You were signed out" instead of the fresh-install reset. "gone" resets as
 *   on a 200, "refused" goes through the renewal like a 401, "alive" or no answer is "unconfirmed" (H2).
 */
export async function deleteAccount(router: Nav, call: () => Promise<AccountDeleted> = api.deleteAccount): Promise<DeleteOutcome> {
  const checked = async (): Promise<AccountDeleted> => {
    const sent = getToken(); // per attempt: the retry after a renewal carries the new token
    try {
      return await call(); // reads its header in this same tick (serverThenReset relies on it)
    } catch (e) {
      if (e instanceof ApiError && e.status === 401 && isNoLongerRegistered(e.detail)) return GONE;
      if (!sent || !isNoAnswer(e)) throw e;
      const state = await tokenState(sent);
      if (state === "account_gone" || state === "unregistered") return GONE;
      if (state === "refused" || state === "session_closed") throw new ApiError(401, "/auth/check", "refused");
      throw new Unconfirmed();
    }
  };
  const r = await serverThenReset(router, checked);
  if (r.ok) {
    const appleRevoked = r.value?.apple_revoked ?? null;
    // Apple's settings keep listing the app until the driver removes it there; nothing to show on a fresh phone.
    if (appleRevoked === false) console.warn("[account] Apple did not take the revocation");
    return { ok: true, appleRevoked };
  }
  const e = r.error;
  if (e instanceof Unconfirmed) return { ok: false, reason: "unconfirmed" };
  if (!(e instanceof ApiError)) return { ok: false, reason: "unconfirmed" }; // no answer and no token to check with
  if (e.status === 409) {
    const code = e.code === "last_owner" || e.code === "not_an_account" ? e.code : null;
    return { ok: false, reason: "refused", code, message: code === "last_owner" ? LAST_OWNER : REFUSED };
  }
  if (e.status === 503 && e.detail !== null) return { ok: false, reason: "not_removed" };
  // A 401 the renewal could not fix: the session may have been closed, or the account deleted elsewhere. Not known.
  if (e.status === 401) return { ok: false, reason: "unconfirmed" };
  return { ok: false, reason: "failed" };
}

/** "Clear memory" on the history screen. An account clears the conversations and what the copilot learned from them
 *  (DELETE /api/memory/history); the truck profile and the trips stay. An access-code sign-in has only the older
 *  full wipe (DELETE /api/memory), as before. */
export const clearConversations = (): Promise<unknown> => (signedInWithAccount() ? api.clearHistory() : api.clearMemory());

const REFUSED = "This account can't be deleted from the app. Write to support@truckbuddy.io.";
/** The 409 `last_owner` (backend services/account_api.py LAST_OWNER): the same words, translated in ru.ts. After it
 *  nothing was deleted. */
const LAST_OWNER =
  "This account is the only owner of a fleet that still has drivers or a stored key. Write to support@truckbuddy.io to close the fleet or hand it over.";
