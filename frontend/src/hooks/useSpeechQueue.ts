import { useCallback, useEffect, useRef, useState } from "react";

import { absoluteUrl, api } from "../api";
import { logAnnouncement, type AnnouncementKind } from "../dev/announcements";
import { speakLocal, stopLocal } from "../voice/localTts";
import { preempts, SpeechQueue, type QueuedSpeech, type SpeechItem } from "../voice/queue";
import { isOnline } from "./useOnline";
import type { useVoice } from "./useVoice";

type Voice = Pick<ReturnType<typeof useVoice>, "playAndWait" | "stopSpeaking" | "isSpeaking" | "isRecording" | "holdPlayback">;

export type SayOptions = Omit<SpeechItem, "text">;

/** Idle poll while something waits (the copilot is talking, or the ambient minute has not passed). */
const POLL_MS = 500;
const LOG_KIND: Record<SpeechItem["kind"], AnnouncementKind> = { maneuver: "maneuver", ambient: "advisory", system: "system" };

/**
 * Plays the speech queue through TTS + the shared player. `isBusy()` (listening, thinking, a
 * copilot reply on air) pauses it; the queue's own clip does not count as busy. While it drains
 * the voice hook holds the playback session, so the recorder is not re-armed between two lines
 * (that flips the audio mode twice per clip and clicks on iOS); the hold is released as soon as
 * the queue is empty. `onPlay` fires as each line starts (captions). Offline (NetInfo or a failed
 * API call), or when /api/speak fails, the line goes through the phone's own voice instead
 * (src/voice/localTts.ts): the guidance must stay audible in a dead zone.
 */
export function useSpeechQueue(voice: Voice, isBusy: () => boolean, onPlay?: (item: QueuedSpeech) => void) {
  const [q] = useState(
    () =>
      new SpeechQueue({
        onDrop: (item, why) => {
          if (__DEV__ && why !== "replaced") logAnnouncement(`${item.text} (${why})`, "skipped");
        },
      }),
  );
  const playing = useRef<QueuedSpeech | null>(null);
  /** The line the phone's own voice is reading right now (stopSpeaking() only cuts the player). */
  const localPlaying = useRef<QueuedSpeech | null>(null);
  const draining = useRef(false);
  const poll = useRef<ReturnType<typeof setTimeout> | null>(null);
  const busyRef = useRef(isBusy);
  const onPlayRef = useRef(onPlay);
  useEffect(() => {
    busyRef.current = isBusy;
    onPlayRef.current = onPlay;
  });
  const { playAndWait, stopSpeaking, isSpeaking, isRecording, holdPlayback } = voice;

  const pump = useCallback(async () => {
    if (draining.current) return;
    draining.current = true;
    if (poll.current) clearTimeout(poll.current);
    poll.current = null;
    try {
      for (;;) {
        const busy = busyRef.current() || isRecording() || (isSpeaking() && !playing.current);
        const item = q.take(Date.now(), busy);
        if (!item) break;
        holdPlayback(true);
        playing.current = item;
        if (__DEV__) logAnnouncement(item.text, LOG_KIND[item.kind]);
        onPlayRef.current?.(item);
        const maxMs = Math.min(15000, 1500 + item.text.length * 65);
        let cloud: string | null = null;
        if (isOnline()) {
          try {
            cloud = absoluteUrl((await api.speak(item.text)).audio_url);
          } catch (e) {
            console.log(`[speech] "${item.text}" cloud voice failed (${String(e)}), using the local voice`);
          }
        }
        // Cut off (preempted) while the audio was being made: play the newer line instead.
        if (playing.current !== item) continue;
        try {
          if (cloud) await playAndWait(cloud, maxMs);
          else {
            localPlaying.current = item;
            await speakLocal(item.text, maxMs);
            if (localPlaying.current === item) localPlaying.current = null;
          }
        } catch (e) {
          console.log(`[speech] "${item.text}" failed: ${String(e)}`);
        }
        if (playing.current === item) playing.current = null;
      }
    } finally {
      draining.current = false;
      if (q.pending() > 0) poll.current = setTimeout(pump, POLL_MS);
      else holdPlayback(false);
    }
  }, [q, isSpeaking, isRecording, playAndWait, holdPlayback]);

  const say = useCallback(
    (text: string, opts: SayOptions) => {
      const item = q.enqueue({ text, ...opts }, Date.now());
      const cur = playing.current;
      if (cur && preempts(cur, item)) {
        if (__DEV__) logAnnouncement(`${cur.text} (cut off)`, "skipped");
        playing.current = null;
        stopSpeaking(); // resolves the current playAndWait; the loop takes the new line next
        if (localPlaying.current === cur) {
          localPlaying.current = null;
          stopLocal(); // resolves speakLocal through onStopped
        }
      }
      pump();
    },
    [q, pump, stopSpeaking],
  );

  const clear = useCallback(() => q.clear(), [q]);

  useEffect(
    () => () => {
      if (poll.current) clearTimeout(poll.current);
      q.clear();
    },
    [q],
  );

  return { say, clear, queue: q };
}
