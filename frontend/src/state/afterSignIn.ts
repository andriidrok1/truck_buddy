// What happens right after the server accepts a sign-in (src/signin/index.ts), before the token is stored and the
// root layout leaves the sign-in screen.
//
// - The account already has a profile on the server (`has_profile`): a returning driver on a new phone or after a
//   reinstall. Onboarding is skipped, "How to use" does not open by itself (they have seen the app), the server's
//   truck is pulled as soon as the token is in (src/data/profile.ts useProfileSync), and "Welcome back" shows once.
// - Otherwise (a new account, or one that never finished setup): onboarding as before, with the name the provider
//   gave already in the name step.

import { setOnboarded } from "../hooks/useTruckProfile";
import { saveAccount, setSignInNotice, setWelcomeBack, type Provider } from "./account";
import { markHowToSeen } from "./howTo";

export type SignedIn = { new_account: boolean; has_profile: boolean; name?: string | null; email?: string | null };

export type AfterSignIn = "restore" | "onboarding";

/** `has_profile` decides alone: an account that signed in before but never finished setup still gets onboarding. */
export function afterSignIn(r: Pick<SignedIn, "has_profile" | "new_account">): AfterSignIn {
  return r.has_profile ? "restore" : "onboarding";
}

/** Runs before the token is stored: by the time the map screen asks "is this driver onboarded?", the answer is on
 *  disk. Returns what was decided. */
export async function prepareSignedIn(r: SignedIn, provider: Provider): Promise<AfterSignIn> {
  setSignInNotice(null);
  await saveAccount({ name: r.name ?? null, email: r.email ?? null, provider });
  const plan = afterSignIn(r);
  if (plan === "restore") {
    await setOnboarded();
    // "seen", not "due": the screen does not open by itself and the dot on "?" is off, as on the phone they had.
    await markHowToSeen();
    setWelcomeBack(true);
  }
  return plan;
}
