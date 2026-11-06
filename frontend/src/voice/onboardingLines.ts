// The onboarding conversation's steps and lines (moved verbatim from app/onboarding.tsx).
import { t } from "../i18n";

export type Step = "name" | "truck" | "prefs" | "done";
export const STEPS: Step[] = ["name", "truck", "prefs", "done"];
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
      return "Hi! I'm TruckBuddy, your copilot on the road. Let's get to know each other. What's your name?";
    case "truck":
      return `Nice to meet you${who}! Now tell me about your rig: height, trailer length, weight, and whether you haul hazmat.`;
    case "prefs":
      return "Here are a few things I can keep in mind on every route. If any of these matter to you, just say so and I'll remember.";
    default:
      return `All set${who}! Tap me anytime and tell me where you need to go.`;
  }
}

/** The short "thought" shown next to the avatar: only the question. */
export function thought(step: Step, name: string) {
  switch (step) {
    case "name":
      return "What's your name?";
    case "truck":
      return "Tell me about your rig";
    case "prefs":
      return "Anything here matter to you?";
    default:
      return name ? `All set, ${name}!` : "All set!";
  }
}
