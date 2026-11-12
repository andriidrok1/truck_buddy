// What the account part of the truck screen does (src/components/AccountActions.tsx): sign out of this phone, sign
// out of every phone, delete the account. An access-code sign-in keeps its own "Sign out" and "Delete my data"
// (src/state/firstLaunch.ts); none of these is offered to it.

import { api, ApiError, isNoAnswer, logout, tokenState, why401, type AccountDeleted, type RefusalCode } from "../api";
import { getToken, identitySub } from "../auth";
import { storage } from "../utils/storage";
import { serverThenReset, SignedOutByServer, signedOutReset, WipeBusy, type Nav } from "./firstLaunch";

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
  | { ok: false; reason: "unconfirmed" }
  /** The server turned the sign-in down (the renewal was rejected, or the session is closed): the phone is on the
   *  sign-in screen with "You were signed out". Nothing says the account was deleted. */
  | { ok: false; reason: "signed_out" }
  /** "Delete my data" is under way on this phone: nothing was sent for this one. */
  | { ok: false; reason: "busy" };

class Unconfirmed extends Error {}

/**
 * Treated as a deletion that went through, and the phone resets to a fresh install. Only on our 401 with `code`
 * "account_gone", or, from a server without codes, on "no longer registered" after this phone sent a DELETE that got
 * no answer (UNANSWERED_KEY): without codes that text is also what a closed session hears (review of #173, H-1).
 * Nothing on this phone or in the copy ever says "your account was deleted" (M3): the driver sees the first launch.
 */
const GONE: AccountDeleted = { ok: true, apple_revoked: null, deleted: 0, trip_events: 0, profile: false, sessions: 0 }; // counts and Apple's answer are not known here

/** On disk: this phone sent DELETE /api/account and got no answer, for the account (token `sub`) in the value. It
 *  survives a restart; a sign-out removes it with the rest of the driver's keys (IDENTITY_KEYS). */
export const UNANSWERED_KEY = "account_delete_unanswered_v1";
const markUnanswered = () => storage.setItem(UNANSWERED_KEY, identitySub() ?? "");
const clearUnanswered = () => storage.removeItem(UNANSWERED_KEY);
async function sentUnanswered(): Promise<boolean> {
  return (await storage.getItem<string | null>(UNANSWERED_KEY, null).catch(() => null)) === (identitySub() ?? "");
}

/**
 * "Delete account and data": DELETE /api/account with every other request held (serverThenReset), then the phone
 * back to a fresh install (the language screen). On a 409 or any failure nothing on the phone changes.
 *
 * Our 401 on the DELETE (review of #173, H-1): `account_gone` counts as a 200 (a retry after a deletion that went
 * through). `session_closed` (logout-all from another phone, a reused refresh token, an idle session: the account
 * is alive) is the forced sign-out with "You were signed out", never "deleted". "No longer registered" without a
 * code (an older server) counts as a 200 only when this phone already sent a DELETE that got no answer, and is the
 * forced sign-out otherwise. Any other 401 (the access token expired while the screen was open) goes to
 * serverThenReset, which renews the token outside the hold and sends the delete once more. That is the only retry.
 *
 * No answer: GET /api/auth/check with the same token, still under the hold, so no other request gets the 401
 * first. The same rules (the DELETE that just went unanswered counts), "refused" goes through the renewal like a
 * 401, "alive" or no answer is "unconfirmed" (H2).
 */
export async function deleteAccount(router: Nav, call: () => Promise<AccountDeleted> = api.deleteAccount): Promise<DeleteOutcome> {
  const checked = async (): Promise<AccountDeleted> => {
    const sent = getToken(); // per attempt: the retry after a renewal carries the new token
    try {
      return await call(); // reads its header in this same tick (serverThenReset relies on it)
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        const why = why401(e.detail, e.code);
        if (why === "account_gone") return GONE;
        if (why === "session_closed") throw new SignedOutByServer();
        if (why === "unregistered") {
          if (await sentUnanswered()) return GONE;
          throw new SignedOutByServer();
        }
        throw e; // "refused": the renewal in serverThenReset
      }
      if (!sent || !isNoAnswer(e)) {
        if (e instanceof ApiError) await clearUnanswered(); // the server answered: the account was still there
        throw e;
      }
      await markUnanswered();
      const state = await tokenState(sent);
      if (state === "account_gone" || state === "unregistered") return GONE; // unregistered: after our unanswered DELETE
      if (state === "session_closed") throw new SignedOutByServer();
      if (state === "refused") throw new ApiError(401, "/auth/check", "refused");
      throw new Unconfirmed();
    }
  };
  const r = await serverThenReset(router, checked, "account");
  if (r.ok) {
    const appleRevoked = r.value?.apple_revoked ?? null;
    // Apple's settings keep listing the app until the driver removes it there; nothing to show on a fresh phone.
    if (appleRevoked === false) console.warn("[account] Apple did not take the revocation");
    return { ok: true, appleRevoked };
  }
  const e = r.error;
  if (e instanceof WipeBusy) return { ok: false, reason: "busy" };
  if (e instanceof SignedOutByServer) return { ok: false, reason: "signed_out" };
  if (e instanceof Unconfirmed) return { ok: false, reason: "unconfirmed" };
  if (!(e instanceof ApiError)) return { ok: false, reason: "unconfirmed" }; // no answer and no token to check with
  if (e.status === 409) {
    const code = e.code === "last_owner" || e.code === "not_an_account" ? e.code : null;
    return { ok: false, reason: "refused", code, message: code === "last_owner" ? LAST_OWNER : REFUSED };
  }
  if (e.status === 503 && e.detail !== null) return { ok: false, reason: "not_removed" };
  // A 401 the renewal could not settle (no answer to it, or the retry got a 401 again): not known.
  if (e.status === 401) return { ok: false, reason: "unconfirmed" };
  return { ok: false, reason: "failed" };
}

/** "Clear memory" on the history screen, for every kind of sign-in (account, access code, device): the conversations
 *  and what the copilot learned from them (DELETE /api/memory/history, any driver token). The truck profile, the
 *  trips and the visit counts stay, on the server and on the phone. DELETE /api/memory is "Delete my data" only: it
 *  takes the stored truck too. */
export const clearConversations = (): Promise<unknown> => api.clearHistory();

const REFUSED = "This account can't be deleted from the app. Write to support@truckbuddy.io.";
/** The 409 `last_owner` (backend services/account_api.py LAST_OWNER): the same words, translated in ru.ts. After it
 *  nothing was deleted. */
const LAST_OWNER =
  "This account is the only owner of a fleet that still has drivers or a stored key. Write to support@truckbuddy.io to close the fleet or hand it over.";
