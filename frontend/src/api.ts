import { getTruckProfile } from "./hooks/useTruckProfile";
import { uploadForm } from "./utils/upload";
import type { AgentResponse, Category, LatLng, ParkingCheck, PlacesResponse, Place, RouteInfo } from "./types";

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
  onboardingParse: async (uri: string, step: "name" | "truck" | "prefs") => {
    const web = uri.startsWith("blob:") || uri.startsWith("data:");
    const res = await uploadForm(`${BASE}/onboarding/parse`, {
      fields: { step },
      files: { audio: { uri, name: web ? "speech.webm" : "speech.m4a", mimeType: web ? "audio/webm" : "audio/mp4" } },
    });
    if (!res.ok) throw new Error(`/onboarding/parse failed: ${res.status}`);
    return (await res.json()) as { transcript: string; updates: Record<string, unknown> };
  },
  voice: async (uri: string, at: LatLng, activePlace?: Place, signal?: AbortSignal, sessionId?: string) => {
    const web = uri.startsWith("blob:") || uri.startsWith("data:");
    const fields: Record<string, string> = { lat: String(at.lat), lng: String(at.lng) };
    if (sessionId) fields.session_id = sessionId;
    if (activePlace) fields.active_place = JSON.stringify(activePlace);
    fields.truck_profile = JSON.stringify(getTruckProfile());
    const res = await uploadForm(`${BASE}/voice`, {
      fields,
      files: { audio: { uri, name: web ? "speech.webm" : "speech.m4a", mimeType: web ? "audio/webm" : "audio/mp4" } },
      signal,
    });
    if (!res.ok) throw new Error(`/voice failed: ${res.status}`);
    return (await res.json()) as AgentResponse;
  },
};
