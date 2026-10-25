import { absoluteUrl, api } from "../api";
import type { Place } from "../types";

type Deps = {
  /** Still the turn that asked (driver hasn't moved on). */
  isCurrent: () => boolean;
  /** Driver is talking to the copilot right now: don't speak over them. */
  busy: () => boolean;
  say: (text: string, audioUrl?: string | null) => Promise<void>;
};

/**
 * "How do I get in?" before the satellite check finished: after "Let me look at the satellite"
 * wait up to 15 s for the answer and speak it. On timeout say so; the late answer is spoken
 * in the background once ready. Resolves after the first answer was spoken (the dialog goes on).
 */
export async function parkingFollowup(place: Place, spoken: Promise<unknown>, deps: Deps) {
  // One retry: a dropped connection (server restart, bad cell coverage) must not leave the driver hanging.
  const ask = (waitS: number) => api.parkingAnswer(place, waitS).catch(() => api.parkingAnswer(place, waitS));
  try {
    const first = ask(15);
    await spoken;
    const r = await first;
    if (!deps.isCurrent()) return;
    await deps.say(r.reply, r.audio_url);
    if (r.status !== "pending") return;
    ask(45)
      .then((late) => (late.status === "ready" && !deps.busy() ? deps.say(late.reply, late.audio_url) : undefined))
      .catch((e) => console.warn("[parking] late answer failed", e));
  } catch (e) {
    console.warn("[parking] follow-up failed", e);
    await spoken;
    if (!deps.isCurrent()) return;
    const text = PARKING_FAILED;
    const audio = await api.speak(text).catch(() => null);
    await deps.say(text, audio?.audio_url);
  }
}

const PARKING_FAILED = "Sorry, I couldn't get the satellite view right now. Ask me again in a minute.";

export const toAudio = (u?: string | null) => (u ? absoluteUrl(u) : null);
