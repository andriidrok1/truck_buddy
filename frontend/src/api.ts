import { authHeader, requestCode, setToken } from "./auth";
import { eldHeader } from "./eld";
import { getTruckProfile } from "./hooks/useTruckProfile";
import { uploadForm } from "./utils/upload";
import type { ActiveRoute, AgentResponse, Approach, Category, LatLng, ParkingCheck, PlacesResponse, Place, RouteInfo, UiState } from "./types";
import { BASE } from "./url";

// ./url throws at load time when EXPO_PUBLIC_BACKEND_URL is missing, so importing this module still fails loudly.
export { absoluteUrl, apiUrl } from "./url";

export type MemoryTurn = { role: "driver" | "copilot"; text: string; at: string };

export type RouteResult = { place: Place; route: RouteInfo; parking?: ParkingCheck | null };

/** POST /api/reroute (truck_profile is added by the client). */
export type RerouteBody = {
  lat: number;
  lng: number;
  place: Place;
  /** RouteInfo.handle of the route being left; null when the backend never sent one. */
  handle?: string | null;
  /** Degrees 0..359 from the GPS fix; null when standing still / unknown. */
  heading?: number | null;
  /** Index into RouteInfo.section_starts of the section the truck was in (0 without sections). */
  section_index: number;
  /** Metres driven into that section. */
  traveled_m: number;
  via?: [number, number][];
};

/** A rejected token (expired / code changed) sends the driver back to the access-code screen. */
function check(res: Response, path: string) {
  if (res.status === 401) setToken(null);
  if (!res.ok) throw new Error(`${path} failed: ${res.status}`);
}

/**
 * A sleeping or crashed host answers 200 with an HTML page; parsing it as JSON used to surface as
 * "Sorry, I didn't catch that". Treat anything that is not JSON as a dead connection instead.
 */
async function json<T>(res: Response, path: string): Promise<T> {
  const type = res.headers.get("content-type") ?? "";
  if (!/json/i.test(type)) throw new Error(`${path} failed: network (unexpected ${type || "empty"} response)`);
  try {
    return (await res.json()) as T;
  } catch {
    throw new Error(`${path} failed: network (malformed JSON)`);
  }
}

const DEFAULT_TIMEOUT_MS = 30000;

/** Aborts after `ms`, or as soon as the caller's own signal aborts (no AbortSignal.any on Hermes). */
function deadline(ms: number, outer?: AbortSignal | null) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), ms);
  const onAbort = () => ctrl.abort();
  if (outer?.aborted) ctrl.abort();
  else outer?.addEventListener("abort", onAbort);
  return {
    signal: ctrl.signal,
    done: () => {
      clearTimeout(timer);
      outer?.removeEventListener("abort", onAbort);
    },
  };
}

/** A stalled cell connection must never leave a screen spinning forever: every call has a deadline. */
async function request<T>(path: string, init?: RequestInit & { timeoutMs?: number }): Promise<T> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, ...rest } = init ?? {};
  const d = deadline(timeoutMs, rest.signal);
  try {
    const res = await fetch(`${BASE}${path}`, {
      ...rest,
      signal: d.signal,
      headers: { "Content-Type": "application/json", ...authHeader(), ...(rest.headers ?? {}) },
    });
    check(res, path);
    return json<T>(res, path);
  } finally {
    d.done();
  }
}

/** Thrown when the server has open sign-up turned off: the driver needs an access code. */
export class CodeRequiredError extends Error {}

/** Open sign-up: a device token without a code. */
export async function registerDevice() {
  let res: Response;
  const d = deadline(DEFAULT_TIMEOUT_MS);
  try {
    res = await fetch(`${BASE}/auth/device`, { method: "POST", signal: d.signal });
  } catch {
    throw new Error("No connection. Check your internet and try again.");
  } finally {
    d.done();
  }
  if (res.status === 403) throw new CodeRequiredError("Access code required");
  if (res.status === 429) throw new Error("Too many tries. Wait a few minutes and try again.");
  if (!res.ok) throw new Error("Something went wrong. Try again.");
  const data = (await res.json()) as { access_token: string };
  await setToken(data.access_token, "driver");
}

export async function redeemAccessCode(code: string) {
  let res: Response;
  const d = deadline(15000);
  try {
    res = await fetch(`${BASE}/auth/access-code`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
      signal: d.signal,
    });
  } catch {
    throw new Error("No connection. Check your internet and try again.");
  } finally {
    d.done();
  }
  if (res.status === 429) throw new Error("Too many tries. Wait a few minutes and try again.");
  if (res.status === 401) throw new Error("That code isn't right. Check it and try again.");
  if (!res.ok) throw new Error("Something went wrong. Try again.");
  const data = await json<{ access_token: string; role?: "driver" | "tester" }>(res, "/auth");
  await setToken(data.access_token, data.role === "tester" ? "tester" : "driver");
  requestCode(false);
}

export const api = {
  memory: () => request<{ turns: MemoryTurn[] }>(`/memory`),
  clearMemory: () => request<{ deleted: number }>(`/memory`, { method: "DELETE" }),
  places: (category: Category, at: LatLng) =>
    request<PlacesResponse>(`/places?category=${category}&lat=${at.lat}&lng=${at.lng}`),
  route: (from: LatLng, place: Place, skipParking = false) =>
    request<RouteResult>(`/route`, {
      method: "POST",
      body: JSON.stringify({ lat: from.lat, lng: from.lng, place, skip_parking: skipParking, truck_profile: getTruckProfile() }),
    }),
  /**
   * Off-route reroute: the engine resumes the server-side plan (`handle`, section, metres into it)
   * instead of planning from scratch. Same shape back as /route, with a new `handle`.
   * 503 = routing down, 422 = outside the US; the caller falls back to /route on any failure.
   * 15 s: longer than that and the driver has long passed the next turn anyway.
   */
  reroute: (body: RerouteBody) =>
    request<RouteResult>(`/reroute`, {
      method: "POST",
      timeoutMs: 15000,
      body: JSON.stringify({ ...body, truck_profile: getTruckProfile() }),
    }),
  parking: (place: Place, approach?: Approach | null) =>
    request<ParkingCheck>(`/parking`, { method: "POST", body: JSON.stringify({ place, approach }), timeoutMs: 60000 }),
  /** Spoken parking answer; waits up to `waitS` for a satellite check in flight. */
  parkingAnswer: (place: Place, waitS: number) =>
    request<{ status: "ready" | "pending"; reply: string; audio_url?: string | null; parking?: ParkingCheck | null }>(
      `/parking/answer`,
      { method: "POST", body: JSON.stringify({ place, wait_s: waitS }), timeoutMs: waitS * 1000 + 30000 },
    ),
  agent: (text: string, at: LatLng) =>
    request<AgentResponse>(`/agent`, {
      method: "POST",
      timeoutMs: 65000,
      headers: eldHeader(), // Motive key: the copilot reads hours of service
      body: JSON.stringify({ text, lat: at.lat, lng: at.lng, truck_profile: getTruckProfile() }),
    }),
  speak: (text: string) => request<{ audio_url: string }>(`/speak`, { method: "POST", body: JSON.stringify({ text }) }),
  /** Pre-fetch nearby places (+ parking checks) so the next voice turn hits warm caches. */
  warm: (at: LatLng) => request<{ ok: boolean }>(`/warm?lat=${at.lat}&lng=${at.lng}`, { method: "POST" }),
  /** Voice turn step 1: speech -> text, shown to the driver right away. */
  transcribe: async (uri: string, signal?: AbortSignal) => {
    const web = uri.startsWith("blob:") || uri.startsWith("data:");
    const d = deadline(20000, signal); // upload + STT; the server itself gives up on STT after 12 s
    try {
      const res = await uploadForm(`${BASE}/voice/transcribe`, {
        fields: {},
        files: { audio: { uri, name: web ? "speech.webm" : "speech.m4a", mimeType: web ? "audio/webm" : "audio/mp4" } },
        headers: { ...authHeader(), ...eldHeader() },
        signal: d.signal,
      });
      check(res as Response, "/voice/transcribe");
      return json<{ transcript: string; timings?: Record<string, number> }>(res, "/voice/transcribe");
    } finally {
      d.done();
    }
  },
  /** Voice turn step 2: text -> action + spoken reply. */
  agentSpeak: (
    text: string,
    at: LatLng,
    active?: ActiveRoute | null,
    signal?: AbortSignal,
    sessionId?: string,
    ui?: UiState,
    heading: number | null = null, // degrees from the GPS fix, null when the truck is standing still
  ) =>
    request<AgentResponse>(`/agent?speak=true`, {
      method: "POST",
      signal,
      timeoutMs: 65000, // the map screen aborts the turn itself at 60 s
      headers: eldHeader(), // Motive key: the copilot reads hours of service
      body: JSON.stringify({
        text,
        lat: at.lat,
        lng: at.lng,
        heading,
        truck_profile: getTruckProfile(),
        session_id: sessionId,
        active_place: active?.place,
        active_distance_mi: active?.route.distance_mi,
        active_duration_min: active?.route.duration_min,
        ui,
      }),
    }),
  /** Breakdown research result (long-poll: waits until the research is done). */
  breakdownResult: (id: string, signal?: AbortSignal) =>
    request<AgentResponse>(`/breakdown/${id}?speak=true`, { signal, timeoutMs: 120000 }),
  onboardingParse: async (uri: string, step: "name" | "truck" | "prefs") => {
    const web = uri.startsWith("blob:") || uri.startsWith("data:");
    const d = deadline(30000);
    try {
      const res = await uploadForm(`${BASE}/onboarding/parse`, {
        fields: { step },
        files: { audio: { uri, name: web ? "speech.webm" : "speech.m4a", mimeType: web ? "audio/webm" : "audio/mp4" } },
        headers: authHeader(),
        signal: d.signal,
      });
      check(res as Response, "/onboarding/parse");
      return json<{ transcript: string; updates: Record<string, unknown> }>(res, "/onboarding/parse");
    } finally {
      d.done();
    }
  },
};
