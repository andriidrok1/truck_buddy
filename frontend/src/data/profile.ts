// Server-side resources through react-query. The truck profile is the first: the server copy is the
// truth once the backend has `/api/me/profile` (fleet-managed profiles land there); until then, and
// whenever the network is away, the local copy in hooks/useTruckProfile.ts is what the app runs on.
// A fleet push is a cache invalidation of ["profile"].

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "../api";
import { ensureTruckProfile, getTruckProfile, saveTruckProfile, type TruckProfile } from "../hooks/useTruckProfile";
import { queryClient } from "../query-client";
import type { TripEventRow } from "../api";

export const PROFILE_KEY = ["profile"] as const;
export const tripEventsKey = (tripId: string) => ["trip-events", tripId] as const;

/** The server has no profile endpoint yet (or no profile for this driver): the local copy stands. */
export const isMissing = (e: unknown) => e instanceof ApiError && (e.status === 404 || e.status === 405);

/** The profile from the server, else the local one; the local copy is kept in step with what the server says. */
export async function fetchProfile(): Promise<TruckProfile> {
  const local = await ensureTruckProfile();
  try {
    const remote = await api.profile();
    const merged = { ...local, ...remote };
    if (JSON.stringify(merged) !== JSON.stringify(local)) await saveTruckProfile(merged);
    return merged;
  } catch (e) {
    if (isMissing(e)) return local;
    throw e;
  }
}

/** Saves locally first (the requests must see the new truck at once), then on the server when it has the endpoint. */
export async function pushProfile(next: TruckProfile): Promise<TruckProfile> {
  await saveTruckProfile(next);
  try {
    return await api.saveProfile(next);
  } catch (e) {
    if (isMissing(e)) return next;
    throw e;
  }
}

export function useProfile() {
  return useQuery({
    queryKey: PROFILE_KEY,
    queryFn: fetchProfile,
    initialData: () => getTruckProfile(),
    staleTime: 5 * 60 * 1000,
    retry: (count, e) => !isMissing(e) && count < 2,
  });
}

export function useSaveProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: pushProfile,
    onMutate: async (next) => {
      await qc.cancelQueries({ queryKey: PROFILE_KEY });
      const previous = qc.getQueryData<TruckProfile>(PROFILE_KEY);
      qc.setQueryData(PROFILE_KEY, next);
      return { previous };
    },
    onError: (_e, _next, ctx) => {
      if (ctx?.previous) qc.setQueryData(PROFILE_KEY, ctx.previous);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: PROFILE_KEY }),
  });
}

/** A fleet push or a profile change elsewhere: the next read goes to the server. */
export const invalidateProfile = () => queryClient.invalidateQueries({ queryKey: PROFILE_KEY });

/** The trip log rows the backend holds for one drive (tester screen). */
export function useTripEvents(tripId: string | null | undefined, enabled = true) {
  return useQuery<TripEventRow[]>({
    queryKey: tripEventsKey(tripId ?? "none"),
    queryFn: () => api.tripEventsOf(tripId as string),
    enabled: enabled && !!tripId,
    staleTime: 15 * 1000,
  });
}
