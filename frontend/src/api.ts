import { getTruckProfile } from "./hooks/useTruckProfile";
import { uploadForm } from "./utils/upload";
import type { ActiveRoute, AgentResponse, Category, LatLng, ParkingCheck, PlacesResponse, Place, RouteInfo, UiState } from "./types";

const ORIGIN = process.env.EXPO_PUBLIC_BACKEND_URL;
const BASE = `${ORIGIN}/api`;

export const absoluteUrl = (path: string) => `${ORIGIN}${path}`;

export const apiUrl = (path: string) => `${BASE}${path}`;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw new Error(`${path} failed: ${res.status}`);
  return (await res.json()) as T;
}

export const api = {
  places: (category: Category, at: LatLng) =>
    request<PlacesResponse>(`/places?category=${category}&lat=${at.lat}&lng=${at.lng}`),
  route: (from: LatLng, place: Place) =>
    request<{ place: Place; route: RouteInfo; parking?: ParkingCheck | null }>(`/route`, {
      method: "POST",
      body: JSON.stringify({ lat: from.lat, lng: from.lng, place, truck_profile: getTruckProfile() }),
    }),
  parking: (place: Place) =>
    request<ParkingCheck>(`/parking`, { method: "POST", body: JSON.stringify({ place }) }),
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
      signal,
    });
    if (!res.ok) throw new Error(`/voice/transcribe failed: ${res.status}`);
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
  onboardingParse: async (uri: string, step: "name" | "truck" | "prefs") => {
    const web = uri.startsWith("blob:") || uri.startsWith("data:");
    const res = await uploadForm(`${BASE}/onboarding/parse`, {
      fields: { step },
      files: { audio: { uri, name: web ? "speech.webm" : "speech.m4a", mimeType: web ? "audio/webm" : "audio/mp4" } },
    });
    if (!res.ok) throw new Error(`/onboarding/parse failed: ${res.status}`);
    return (await res.json()) as { transcript: string; updates: Record<string, unknown> };
  },
};
