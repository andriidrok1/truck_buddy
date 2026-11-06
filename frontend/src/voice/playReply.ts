import { absoluteUrl } from "../api";
import { replyCaption, type Caption } from "../dialog/caption";
import type { useVoice } from "../hooks/useVoice";
import type { AgentResponse } from "../types";

type Voice = Pick<ReturnType<typeof useVoice>, "playAndWait" | "preload">;

/**
 * Shows a copilot reply and plays its audio (one clip, or two with the second buffered while the first plays).
 * Moved verbatim from app/index.tsx (`sayReply`); resolves when the audio is done.
 */
export function playReply(voice: Voice, setCaption: (c: Caption) => void, res: AgentResponse, isCurrent: () => boolean = () => true): Promise<void> {
  setCaption(replyCaption(res.reply, res.transcript || undefined, Date.now(), res.maps_sources));
  if (!res.audio_url) return Promise.resolve();
  // The playback timeouts are sized from the text behind each clip: the server says how it split
  // the reply when it can; older backends leave that to the sentence split.
  const parts = res.reply_parts?.length ? res.reply_parts : res.reply.split(/(?<=[.!?])\s+/);
  const [first, ...restParts] = parts;
  const rest = restParts.join(" ");
  const done = voice.playAndWait(absoluteUrl(res.audio_url), Math.min(20000, 1500 + first.length * 65));
  if (!res.audio_url_2) return done;
  // Buffer the second half while the first plays, so it starts right after it (<150 ms gap).
  const url2 = absoluteUrl(res.audio_url_2);
  const next = voice.preload(url2);
  const available = fetch(url2, { headers: { Range: "bytes=0-0" } })
    .then((r) => r.ok)
    .catch(() => false);
  return done.then(async () => {
    if (!isCurrent()) {
      next.remove();
      return;
    }
    if (!(await available)) {
      console.warn(`[voice] second half unavailable (${url2}); showing the full text instead`);
      next.remove();
      setCaption(replyCaption(res.reply, res.transcript || undefined, Date.now(), res.maps_sources));
      return;
    }
    if (!isCurrent()) {
      next.remove();
      return;
    }
    return voice.playAndWait(next, Math.min(20000, 1500 + (rest || res.reply).length * 65));
  });
}
