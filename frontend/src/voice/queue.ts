// What the copilot says on its own, in order: a maneuver that is due now beats everything, the
// far/near maneuver calls and road closures come next, then weather, speed alerts and reviews
// as "ambient" lines that never crowd the driver (one a minute at most). Pure: no timers, no
// audio; the hook (useSpeechQueue) feeds it `now` and plays what it hands back.

export const PRIORITY = {
  MANEUVER_NOW: 0,
  MANEUVER: 1,
  CLOSURE_CHAIN: 1,
  WEATHER: 2,
  SPEED: 3,
  REVIEWS: 4,
} as const;

/** Priorities from here up are ambient: nice to know, never urgent. */
export const AMBIENT_FROM = 2;
export const AMBIENT_GAP_MS = 60000;
export const isAmbient = (priority: number) => priority >= AMBIENT_FROM;

export type SpeechKind = "maneuver" | "ambient" | "system";

export type SpeechItem = {
  text: string;
  priority: number;
  /** Same key = same subject: a newer line replaces the older one still waiting. */
  key?: string;
  /** Not spoken once this old (a maneuver call after the turn is noise). */
  ttlMs?: number;
  kind: SpeechKind;
};

export type QueuedSpeech = SpeechItem & {
  /** Enqueue time (epoch ms). */
  at: number;
  /** Enqueue order, for FIFO within a priority. */
  seq: number;
};

export type DropReason = "expired" | "replaced" | "cleared";

export type SpeechQueueOptions = {
  ambientGapMs?: number;
  /** Something left the queue unspoken (the dev log shows it). */
  onDrop?: (item: QueuedSpeech, why: DropReason) => void;
};

/**
 * Should `incoming` cut `current` off while it plays? A due maneuver ("Turn right.") cuts
 * anything else; a maneuver call or closure cuts an ambient line. Nothing cuts a due maneuver,
 * and ambient never cuts anything.
 */
export function preempts(current: Pick<SpeechItem, "priority">, incoming: Pick<SpeechItem, "priority">): boolean {
  if (incoming.priority === PRIORITY.MANEUVER_NOW) return current.priority > PRIORITY.MANEUVER_NOW;
  if (incoming.priority < AMBIENT_FROM) return isAmbient(current.priority);
  return false;
}

export class SpeechQueue {
  private items: QueuedSpeech[] = [];
  private seq = 0;
  private lastAmbientAt = -Infinity;
  private readonly ambientGapMs: number;
  private readonly onDrop?: SpeechQueueOptions["onDrop"];

  constructor(opts: SpeechQueueOptions = {}) {
    this.ambientGapMs = opts.ambientGapMs ?? AMBIENT_GAP_MS;
    this.onDrop = opts.onDrop;
  }

  enqueue(item: SpeechItem, now: number): QueuedSpeech {
    if (item.key !== undefined) {
      const i = this.items.findIndex((q) => q.key === item.key);
      if (i >= 0) this.onDrop?.(this.items.splice(i, 1)[0], "replaced");
    }
    const queued: QueuedSpeech = { ...item, at: now, seq: this.seq++ };
    this.items.push(queued);
    return queued;
  }

  /**
   * The next line to speak, or null: nothing there, `busy` (the driver is talking to the copilot
   * or a reply is playing), or only ambient lines are waiting and one played under a minute ago.
   * Expired lines are dropped first, whatever `busy` says.
   */
  take(now: number, busy: boolean): QueuedSpeech | null {
    this.expire(now);
    if (busy || !this.items.length) return null;
    const order = [...this.items].sort((a, b) => a.priority - b.priority || a.seq - b.seq);
    for (const q of order) {
      if (isAmbient(q.priority) && now - this.lastAmbientAt < this.ambientGapMs) continue;
      this.items.splice(this.items.indexOf(q), 1);
      if (isAmbient(q.priority)) this.lastAmbientAt = now;
      return q;
    }
    return null;
  }

  clear() {
    const gone = this.items;
    this.items = [];
    gone.forEach((q) => this.onDrop?.(q, "cleared"));
  }

  /** Lines still waiting (expired ones included until the next take). */
  pending(): number {
    return this.items.length;
  }

  private expire(now: number) {
    const keep: QueuedSpeech[] = [];
    for (const q of this.items) {
      if (q.ttlMs !== undefined && now - q.at >= q.ttlMs) this.onDrop?.(q, "expired");
      else keep.push(q);
    }
    this.items = keep;
  }
}
