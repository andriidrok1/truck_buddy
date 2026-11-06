import { useEffect } from "react";

import { RECORD_DRIVES, recorder } from "../geo/driveRecorder";
import { subscribeReplay, takeReplayRequest } from "../geo/replayRequest";
import { startTripLog, stopTripLog, tripLog } from "../net/tripEvents";
import { playTrace } from "../sim";
import { useTripStore } from "../state/tripStore";
import type { ActiveRoute } from "../types";

const trip = useTripStore.getState;

/**
 * The trip log and the drive recorder for the map screen, moved verbatim from app/index.tsx.
 * `clearSpeech` and `onReplayRoute` are the ones of the first render: the replay subscription is made once.
 */
export function useDriveRecorder(active: ActiveRoute | null, driving: boolean, clearSpeech: () => void, onReplayRoute: () => void) {
  // The trip log mirrors its backlog to storage and flushes once a minute while the screen lives; the
  // drive recorder marks every logged event on its timeline.
  useEffect(() => {
    startTripLog();
    const off = tripLog.onEvent((e) => recorder.mark(e.kind, JSON.stringify(e.data)));
    return () => {
      off();
      stopTripLog();
    };
  }, []);
  // The recorder follows the active route: a reroute, an alternative or a stop lands on its timeline.
  useEffect(() => {
    if (active?.route) recorder.routeChanged(active.route);
  }, [active?.route]);
  // A build for a day on the road records every drive from Go to End route.
  useEffect(() => {
    if (RECORD_DRIVES && driving && active && !recorder.isRecording()) {
      recorder.start({ tripId: active.trip_id ?? null, place: active.place, route: active.route });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [driving]);
  // The tester picked a recorded drive: its own route goes on the map, then its fixes drive the truck.
  useEffect(() => {
    const apply = () => {
      const req = takeReplayRequest();
      if (!req) return;
      const { trace, speedFactor, name } = req;
      if (trace.place && trace.route) {
        clearSpeech();
        trip().startRoute({ place: trace.place, route: trace.route, trip_id: trace.trip_id ?? undefined });
        onReplayRoute();
      }
      playTrace(trace.fixes, speedFactor, { id: `trace-${name}`, label: `Replay: ${name} (${speedFactor}x)` });
    };
    apply();
    return subscribeReplay(apply);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
