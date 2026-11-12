// What happens around the moment the server accepts a sign-in (src/signin/index.ts).
//
// - The account already has a truck on the server (`has_profile`): a returning driver on a new phone or after a
//   reinstall. "Welcome back" comes up and holds the app until the server's profile has landed
//   (src/data/profile.ts, the restore block); only then is the driver marked onboarded, with "How to use" marked
//   seen (they know the app). If the server turns out to have no profile after all, the driver is new: onboarding.
// - Otherwise (a new account, or one that never finished setup): onboarding as before, with the name the provider
//   gave already in the name step.
//
// Order, so a kill at any point leaves nothing misleading on disk:
//   before the token  - only what a signed-out phone may carry: the restore flag (ignored and reset by the next
//                       sign-in), "not onboarded" for a new driver, and a trip held for this same account;
//   the token         - src/api.ts;
//   after the token   - the account's name and email. "Onboarded" is written when the profile lands, never before.

import { beginRestore, cancelRestore, setRestoreSettledHandler } from "../data/profile";
import { clearOnboarded, setOnboarded } from "../hooks/useTruckProfile";
import { tokenSub } from "../utils/jwt";
import { saveAccount, setSignInNotice, setWelcomeBack, type Provider } from "./account";
import { settleHeldTrip } from "./heldTrip";
import { markHowToSeen } from "./howTo";

export type SignedIn = { access_token?: string; new_account: boolean; has_profile: boolean; name?: string | null; email?: string | null };

export type AfterSignIn = "restore" | "onboarding";

/** `has_profile` decides alone: an account that signed in before but never finished setup still gets onboarding. */
export function afterSignIn(r: Pick<SignedIn, "has_profile" | "new_account">): AfterSignIn {
  return r.has_profile ? "restore" : "onboarding";
}

/** Before the token is stored. Returns what was decided. */
export async function prepareSignedIn(r: SignedIn): Promise<AfterSignIn> {
  setSignInNotice(null);
  // A route set aside when the server signed this account out mid-drive comes back; anyone else's is destroyed.
  await settleHeldTrip(tokenSub(r.access_token));
  const plan = afterSignIn(r);
  if (plan === "restore") {
    setWelcomeBack(true);
    await beginRestore();
  } else {
    setWelcomeBack(false);
    await cancelRestore();
    await clearOnboarded(); // whatever an interrupted sign-in left: a new driver always gets onboarding
  }
  return plan;
}

/** After the token is stored. */
export async function finishSignedIn(r: SignedIn, provider: Provider): Promise<void> {
  await saveAccount({ name: r.name ?? null, email: r.email ?? null, provider });
}

// The returning driver's profile was answered (src/data/profile.ts fetchProfile awaits this).
setRestoreSettledHandler(async (found) => {
  if (found) {
    await setOnboarded();
    // "seen", not "due": the screen does not open by itself and the dot on "?" is off, as on the phone they had.
    await markHowToSeen();
  } else {
    setWelcomeBack(false);
    await clearOnboarded();
    // The how-to flag is the phone's (KEPT_ON_SIGN_OUT): whoever saw the screen on it does not get it again.
  }
});
