// The short acknowledgement ("Hang on.") the driver hears while the answer is on its way. Pure:
// no React, no audio; the map screen hands in `play` and the timers, and gets back a plan it
// can cancel the moment the answer (or an empty transcript) arrives.

export type FillerPlan = {
  /** The clip that will play, or null when this turn skips the filler. */
  picked: string | null;
  /**
   * Resolves once nothing more will play: the last clip finished, or a cancel came before
   * the first clip (right away) / between the clips (as soon as the one on air is done).
   */
  done: Promise<void>;
  /** Before the first clip: nothing plays. After it: no second clip. Returns true if nothing played. */
  cancel: () => boolean;
  /** Ms after the plan was made that the first clip started; null until (unless) it does. */
  startedAt: () => number | null;
  /** Ms since the plan was made (the plan is made when the driver stops talking). */
  elapsed: () => number;
};

type TimerId = ReturnType<typeof setTimeout>;

export type FillerOptions = {
  /** Candidates, shortest first: the pick is biased towards the top. */
  clips: string[];
  /** Plays one clip; resolves when it has finished. */
  play: (clip: string) => Promise<void>;
  /** Silence before the first clip. An answer faster than this needs no filler. */
  delayMs?: number;
  /** Measured from the first clip's start: if the plan is still alive, `secondClip` plays once. */
  secondMs?: number;
  secondClip?: string | null;
  /** Probability of playing nothing at all this turn. */
  skipRate?: number;
  random?: () => number;
  /** Clock for `startedAt` / `elapsed` (ms). */
  now?: () => number;
  /** The previous turn's clip; never picked twice in a row. */
  lastClip?: string | null;
  setTimeout?: (fn: () => void, ms: number) => TimerId;
  clearTimeout?: (id: TimerId) => void;
};

/** Biased towards the short clips at the top (`random()**2`); never the same clip twice in a row. */
export function pickFiller(clips: string[], lastClip?: string | null, random: () => number = Math.random): string | null {
  const n = clips.length;
  if (!n) return null;
  let i = Math.min(n - 1, Math.floor(n * random() ** 2));
  if (n > 1 && clips[i] === lastClip) i = (i + 1) % n;
  return clips[i];
}

export function planFiller({
  clips,
  play,
  delayMs = 300,
  secondMs = 4000,
  secondClip = null,
  skipRate = 0,
  random = Math.random,
  now = Date.now,
  lastClip = null,
  setTimeout: schedule = globalThis.setTimeout,
  clearTimeout: unschedule = globalThis.clearTimeout,
}: FillerOptions): FillerPlan {
  const t0 = now();
  const elapsed = () => now() - t0;
  const picked = random() < skipRate ? null : pickFiller(clips, lastClip, random);
  if (picked === null) return { picked: null, done: Promise.resolve(), cancel: () => true, startedAt: () => null, elapsed };
  let startedAt: number | null = null;

  let started = false;
  let cancelled = false;
  let onAir: Promise<void> | null = null; // the clip playing (or queued behind the one playing)
  let secondPending = false; // the second clip's timer is still running
  let secondTimer: TimerId | null = null;
  let resolveDone: () => void = () => {};
  const done = new Promise<void>((r) => (resolveDone = r));
  const settle = () => {
    if (!onAir && !secondPending) resolveDone();
  };
  // Clips never overlap: a clip queued while another plays waits for it (and is dropped if a
  // cancel comes in the meantime). With nothing on air the clip starts right now, synchronously.
  const run = (clip: string) => {
    let start: Promise<void>;
    if (onAir) start = onAir.then(() => (cancelled ? undefined : play(clip)));
    else {
      try {
        start = play(clip);
      } catch {
        start = Promise.resolve();
      }
    }
    const p: Promise<void> = start
      .catch(() => undefined)
      .then(() => {
        if (onAir === p) onAir = null;
        settle();
      });
    onAir = p;
  };

  const firstTimer = schedule(() => {
    started = true;
    startedAt = elapsed();
    if (secondClip) {
      secondPending = true;
      secondTimer = schedule(() => {
        secondPending = false;
        secondTimer = null;
        if (cancelled) settle();
        else run(secondClip);
      }, secondMs);
    }
    run(picked);
  }, delayMs);

  return {
    picked,
    done,
    startedAt: () => startedAt,
    elapsed,
    cancel: () => {
      cancelled = true;
      unschedule(firstTimer);
      if (secondTimer !== null) {
        unschedule(secondTimer);
        secondTimer = null;
      }
      secondPending = false;
      if (!started) resolveDone();
      else settle();
      return !started;
    },
  };
}
