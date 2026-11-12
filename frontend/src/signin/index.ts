// One sign-in from the button to the token: the provider's sheet, the server, the local preparation
// (src/state/afterSignIn.ts). The sign-in screen calls this and shows what comes back.

import { signInApple, signInGoogle } from "../api";
import { finishSignedIn, prepareSignedIn } from "../state/afterSignIn";
import type { Provider } from "../state/account";
import { forgetRememberedName, signInWithApple, withRememberedName } from "./apple";
import { signInWithGoogle } from "./google";

/** "cancelled": the driver closed the provider's sheet (nothing to show). "not_configured": this build has no
 *  Google client ids. Anything else that goes wrong is thrown. */
export type SignInOutcome = "signed_in" | "cancelled" | "not_configured";

/** "provider": the system sheet is up. "server": the provider answered, our server is being asked. */
export type SignInStage = "provider" | "server";

export async function signInWith(provider: Provider, onStage?: (s: SignInStage) => void): Promise<SignInOutcome> {
  onStage?.("provider");
  if (provider === "apple") {
    const res = await signInWithApple();
    if (res.type !== "success") return "cancelled";
    const credential = await withRememberedName(res.credential);
    onStage?.("server");
    const r = await signInApple(credential, prepareSignedIn);
    await forgetRememberedName();
    await finishSignedIn(r, "apple");
    return "signed_in";
  }
  const res = await signInWithGoogle();
  if (res.type !== "success") return res.type;
  onStage?.("server");
  const r = await signInGoogle(res.idToken, prepareSignedIn);
  await finishSignedIn(r, "google");
  return "signed_in";
}
