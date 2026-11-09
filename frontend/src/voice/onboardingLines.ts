// The onboarding conversation's steps and lines (moved verbatim from app/onboarding.tsx).
import { t } from "../i18n";

export type Step = "name" | "work" | "truck" | "prefs" | "done";
export const STEPS: Step[] = ["name", "work", "truck", "prefs", "done"];
export const NOT_CAUGHT = "Sorry, I didn't catch that.";
export const TAP_TO_TALK = "Tap me to answer by voice, or type.";

/** What the copilot says out loud, in the driver's language (src/i18n). */
export function spoken(step: Step, name: string) {
  return t(spokenEn(step, name));
}

function spokenEn(step: Step, name: string) {
  const who = name ? `, ${name}` : "";
  switch (step) {
    case "name":
      return "Hi! I'm TruckBuddy, your copilot on the road. What would you like me to call you?";
    case "work":
      return `Nice to meet you${who}! Is this your own truck, or do you drive for a company? If it's yours, I can keep track of your loads and earnings.`;
    case "truck":
      return "Would you like me to plan routes for your rig's size? If so, tell me its height, trailer length, weight, and whether you haul hazmat.";
    case "prefs":
      return "Here are a few things I can keep in mind on every route. Would you like any of them? Just say which.";
    default:
      return `All set${who}! Tap me anytime and tell me where you need to go.`;
  }
}

/** The short "thought" shown next to the avatar: only the question. */
export function thought(step: Step, name: string) {
  switch (step) {
    case "name":
      return "What should I call you?";
    case "work":
      return "Your own truck, or a company's?";
    case "truck":
      return "Want routes sized for your rig?";
    case "prefs":
      return "Want any of these?";
    default:
      return name ? `All set, ${name}!` : "All set!";
  }
}
