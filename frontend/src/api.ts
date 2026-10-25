import { authHeader, setToken } from "./auth";
import { getTruckProfile } from "./hooks/useTruckProfile";
import { uploadForm } from "./utils/upload";
import type { ActiveRoute, AgentResponse, Approach, Category, LatLng, ParkingCheck, PlacesResponse, Place, RouteInfo, UiState } from "./types";

const ORIGIN = process.env.EXPO_PUBLIC_BACKEND_URL;
const BASE = `${ORIGIN}/api`;

export const absoluteUrl = (path: string) => `${ORIGIN}${path}`;

export const apiUrl = (path: string) => `${BASE}${path}`;

export type MemoryTurn = { role: "driver" | "copilot"; text: string; at: string };

/** A rejected token (expired / code changed) sends the driver back to the access-code screen. */
function check(res: Response, path: string) {
  if (res.status === 401) setToken(null);
  if (!res.ok) throw new Error(`${path} failed: ${res.status}`);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...authHeader(), ...(init?.headers ?? {}) },
  });
  check(res, path);
  return (await res.json()) as T;
}

export async function redeemAccessCode(code: string) {
  let res: Response;
  try {
    res = await fetch(`${BASE}/auth/access-code`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    });
  } catch {
    throw new Error("No connection. Check your internet and try again.");
  }
  if (res.status === 429) throw new Error("Too many tries. Wait a few minutes and try again.");
  if (res.status === 401) throw new Error("That code isn't right. Check it and try again.");
  if (!res.ok) throw new Error("Something went wrong. Try again.");
  await setToken(((await res.json()) as { access_token: string }).access_token);
}

export const api = {
  memory: () => request<{ turns: MemoryTurn[] }>(`/memory`),
  clearMemory: () => request<{ deleted: number }>(`/memory`, { method: "DELETE" }),
  places: (category: Category, at: LatLng) =>
    request<PlacesResponse>(`/places?category=${category}&lat=${at.lat}&lng=${at.lng}`),
  route: (from: LatLng, place: Place, skipParking = false) =>
    request<{ place: Place; route: RouteInfo; parking?: ParkingCheck | null }>(`/route`, {
      method: "POST",
      body: JSON.stringify({ lat: from.lat, lng: from.lng, place, skip_parking: skipParking, truck_profile: getTruckProfile() }),
    }),
  parking: (place: Place, approach?: Approach | null) =>
    request<ParkingCheck>(`/parking`, { method: "POST", body: JSON.stringify({ place, approach }) }),
  /** Spoken parking answer; waits up to `waitS` for a satellite check in flight. */
  parkingAnswer: (place: Place, waitS: number) =>
    request<{ status: "ready" | "pending"; reply: string; audio_url?: string | null; parking?: ParkingCheck | null }>(
      `/parking/answer`,
      { method: "POST", body: JSON.stringify({ place, wait_s: waitS }) },
    ),
  agent: (text: string, at: LatLng) =>
    request<AgentResponse>(`/agent`, {
      method: "POST",
      body: JSON.stringify({ text, lat: at.lat, lng: at.lng, truck_profile: getTruckProfile() }),
    }),
  speak: (text: string) => request<{ audio_url: string }>(`/speak`, { method: "POST", body: JSON.stringify({ text }) }),
  /** Pre-fetch nearby places (+ parking checks) so the next voice turn hits warm caches. */
  warm: (at: LatLng) => request<{ ok: boolean }>(`/warm?lat=${at.lat}&lng=${at.lng}`, { method: "POST" }),
  /** Voice turn step 1: speech -> text, shown to the driver right away. */
  transcribe: async (uri: string, signal?: AbortSignal) => {
    const web = uri.startsWith("blob:") || uri.startsWith("data:");
    const res = await uploadForm(`${BASE}/voice/transcribe`, {
      fields: {},
      files: { audio: { uri, name: web ? "speech.webm" : "speech.m4a", mimeType: web ? "audio/webm" : "audio/mp4" } },
      headers: authHeader(),
      signal,
    });
    check(res as Response, "/voice/transcribe");
    return (await res.json()) as { transcript: string; timings?: Record<string, number> };
  },
  /** Voice turn step 2: text -> action + spoken reply. */
  agentSpeak: (text: string, at: LatLng, active?: ActiveRoute | null, signal?: AbortSignal, sessionId?: string, ui?: UiState) =>
    request<AgentResponse>(`/agent?speak=true`, {
      method: "POST",
      signal,
      body: JSON.stringify({
        text,
        lat: at.lat,
        lng: at.lng,
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
    request<AgentResponse>(`/breakdown/${id}?speak=true`, { signal }),
  onboardingParse: async (uri: string, step: "name" | "truck" | "prefs") => {
    const web = uri.startsWith("blob:") || uri.startsWith("data:");
    const res = await uploadForm(`${BASE}/onboarding/parse`, {
      fields: { step },
      files: { audio: { uri, name: web ? "speech.webm" : "speech.m4a", mimeType: web ? "audio/webm" : "audio/mp4" } },
      headers: authHeader(),
    });
    check(res as Response, "/onboarding/parse");
    return (await res.json()) as { transcript: string; updates: Record<string, unknown> };
  },
};
