// One sign-in from the button to the token: the provider's sheet, the server, the local preparation
// (src/state/afterSignIn.ts). The sign-in screen calls this and shows what comes back.

import { signInApple, signInGoogle } from "../api";
import { prepareSignedIn } from "../state/afterSignIn";
import type { Provider } from "../state/account";
import { signInWithApple } from "./apple";
import { signInWithGoogle } from "./google";

/** "cancelled": the driver closed the provider's sheet (nothing to show). "not_configured": this build has no
 *  Google client ids. Anything else that goes wrong is thrown. */
export type SignInOutcome = "signed_in" | "cancelled" | "not_configured";

export async function signInWith(provider: Provider): Promise<SignInOutcome> {
  if (provider === "apple") {
    const res = await signInWithApple();
    if (res.type !== "success") return "cancelled";
    await signInApple(res.credential, (r) => prepareSignedIn(r, "apple"));
    return "signed_in";
  }
  const res = await signInWithGoogle();
  if (res.type !== "success") return res.type;
  await signInGoogle(res.idToken, (r) => prepareSignedIn(r, "google"));
  return "signed_in";
}
