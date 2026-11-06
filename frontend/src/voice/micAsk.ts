// The microphone request behind "Allow microphone". Expo Go sometimes never answers it, so after `slowMs` the card
// offers Settings (`onSlow`), but the request is still awaited: a driver who reads the system dialog for longer
// than that and taps Allow gets the card closed, not stuck on "Open Settings" with the mic already allowed
// (UI review 2026-11-05, ui-mic-card-settings-light.png).

export type MicAnswer<P> = { perm: P; late: boolean };

/** Runs `request`; calls `onSlow` once if it takes longer than `slowMs`. `late` tells the caller the driver has
 *  moved on (the card already changed), so the answer should not start listening on its own. */
export async function askWithDeadline<P>(request: () => Promise<P>, slowMs: number, onSlow: () => void, fallback: P): Promise<MicAnswer<P>> {
  let late = false;
  const timer = setTimeout(() => {
    late = true;
    onSlow();
  }, slowMs);
  let perm: P;
  try {
    perm = await request();
  } catch {
    perm = fallback;
  }
  clearTimeout(timer);
  return { perm, late };
}
