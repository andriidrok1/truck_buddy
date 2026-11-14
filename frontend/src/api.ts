import { authHeader, getToken, identityEpoch, requestCode, setToken, tokenHold } from "./auth";
import { AgentResponseSchema, checkContract, RouteResultSchema } from "./api/schemas";
import { eldHeader } from "./eld";
import { ensureTruckProfile, type TruckProfile } from "./hooks/useTruckProfile";
import { isNetworkError, offlineHint } from "./net/offlineHint";
import { after401, isOur401 } from "./net/tokenRenewal";
import { stopsAhead, viaOf } from "./state/tripStore";
import { uploadForm } from "./utils/upload";
import type { components } from "./types.generated";
import type { AppleCredential } from "./signin/apple";
import type { ActiveRoute, AgentResponse, Approach, Category, LatLng, Nudge, NudgeRequest, ParkingCheck, PlacesResponse, Place, RouteInfo, UiState } from "./types";
import { BASE } from "./url";
import { Platform } from "react-native";

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
  /** The drive this re-plan belongs to (trip events). */
  trip_id?: string | null;
};

/** One event of a drive as the backend hands it back (GET /api/trips/{trip_id}/events). */
export type TripEventRow = {
  id: number;
  t: string;
  trip_id: string | null;
  kind: string;
  source: "app" | "server";
  data: Record<string, unknown>;
};

/** A non-2xx answer, with the status and the backend's `detail`, so a caller can tell one 422 from another. */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    path: string,
    public readonly detail: string | null = null,
  ) {
    super(`${path} failed: ${status}${detail ? ` (${detail})` : ""}`);
    this.name = "ApiError";
  }
}

/** /route or /reroute said the router found no truck route between these points (a 422 with that detail; other
 * 422s are "outside the US" or a malformed request, and those keep the generic toast). */
export const isNoTruckRoute = (e: unknown): boolean =>
  e instanceof ApiError && e.status === 422 && /no truck route/i.test(e.detail ?? "");

/** `sent`: the token the request carried. A 401 on the current token first tries a renewal (net/tokenRenewal.ts); the
 *  phone signs out only when the server rejects the identity, never because the network is down. A request sent
 *  without the token (or with one already replaced) says nothing about the driver's sign-in. `renew: false`: the
 *  caller already went through that (request() after its retry). */
async function check(res: Response, path: string, sent: string | null, renew = true): Promise<void> {
  if (renew && sent !== null && sent === getToken() && (await isOur401(res))) await after401(sent);
  if (res.ok) return;
  let detail: string | null = null;
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body?.detail === "string") detail = body.detail;
  } catch {
    // not JSON: the status is all we know
  }
  throw new ApiError(res.status, path, detail);
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

/** AgentRequest.speed_mps accepts 0..60; the GPS reports null or a negative value when it has no idea. */
const MAX_SPEED_MPS = 60;

/** GPS speed as the agent takes it: m/s to one decimal, capped; undefined (left out) when unknown. */
export function speedForAgent(speed: number | null | undefined): number | undefined {
  if (typeof speed !== "number" || !Number.isFinite(speed) || speed < 0) return undefined;
  return Math.round(Math.min(speed, MAX_SPEED_MPS) * 10) / 10;
}

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

/**
 * A failed fetch on a link that still shows bars is how a dead zone looks: mark the link offline
 * for a short window (useOnline reads it); an answer of any status clears the mark.
 */
async function fetchTracked(url: string, init: RequestInit): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch (e) {
    if (!init.signal?.aborted && isNetworkError(e)) offlineHint.mark(Date.now());
    throw e;
  }
  offlineHint.clear();
  return res;
}

/** A stalled cell connection must never leave a screen spinning forever: every call has a deadline. */
/** Waits out a "Delete my data" hold (auth.ts), then the token the request will carry. Nothing is awaited when no
 *  hold is up, so the header is read in the same tick the call was made (the DELETE itself relies on that). */
async function heldToken(): Promise<string | null> {
  const hold = tokenHold();
  if (hold) await hold;
  return getToken();
}

/** An answer that arrives after the driver it was asked for has signed out is thrown away here, so no handler
 *  further up writes it into the phone of whoever signed in since (a truck change, a route, a parking check). */
function stillSameDriver(who: number, path: string): void {
  if (identityEpoch() !== who) throw new Error(`${path} dropped: the driver signed out`);
}

async function request<T>(path: string, init?: RequestInit & { timeoutMs?: number }): Promise<T> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, ...rest } = init ?? {};
  const d = deadline(timeoutMs, rest.signal);
  try {
    const hold = tokenHold();
    if (hold) await hold;
    const send = () =>
      fetchTracked(`${BASE}${path}`, {
        ...rest,
        signal: d.signal,
        headers: { "Content-Type": "application/json", ...authHeader(), ...(rest.headers ?? {}) },
      });
    let sent = getToken();
    const who = identityEpoch();
    let res = await send();
    if (res.status === 401 && sent !== null && identityEpoch() !== who) {
      // The driver this request was made for has signed out since (and someone else may be signed in by now):
      // it is never sent again under another identity.
      await check(res, path, sent, false);
    } else if (sent !== null && (await isOur401(res))) {
      // An expired token: renew once (or take the one another request just renewed) and send it again.
      const now = getToken();
      const replaced = now !== null && now !== sent;
      if (replaced || (now === sent && (await after401(sent)))) {
        sent = getToken();
        res = await send();
      }
      await check(res, path, sent, false);
    } else {
      await check(res, path, sent);
    }
    stillSameDriver(who, path);
    return json<T>(res, path);
  } finally {
    d.done();
  }
}

/** Thrown when the server has open sign-up turned off: the driver needs an access code. */
export class CodeRequiredError extends Error {}

/** What POST /api/auth/apple and /api/auth/google answer (backend services/account_api.py SignInResponse). */
export type SignInResponse = components["schemas"]["SignInResponse"];
type AppleSignInBody = components["schemas"]["AppleSignInRequest"];
type GoogleSignInBody = components["schemas"]["GoogleSignInRequest"];

/** A sign-in the server did not complete. `status` 0: no answer at all (offline, timed out). */
export class SignInError extends Error {
  constructor(public readonly status: number) {
    super(`sign-in failed: ${status || "network"}`);
    this.name = "SignInError";
  }
}

const signInPlatform = (): "ios" | "android" | undefined => (Platform.OS === "ios" || Platform.OS === "android" ? Platform.OS : undefined);

/**
 * Trades a provider token for the account's token pair and stores it (`via` "account"). `prepare` runs between the
 * server's answer and the token going in: the moment the token is stored the root layout leaves the sign-in screen,
 * so whatever the next screen reads (is this driver onboarded? is "Welcome back" due?) is written first.
 */
async function signIn(path: "/auth/apple" | "/auth/google", body: AppleSignInBody | GoogleSignInBody, prepare?: (r: SignInResponse) => unknown): Promise<SignInResponse> {
  let res: Response;
  const d = deadline(20000);
  try {
    res = await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Lang": authHeader()["X-Lang"] },
      body: JSON.stringify(body),
      signal: d.signal,
    });
  } catch {
    throw new SignInError(0);
  } finally {
    d.done();
  }
  if (!res.ok) throw new SignInError(res.status);
  let data: SignInResponse;
  try {
    data = await json<SignInResponse>(res, path);
  } catch {
    throw new SignInError(0);
  }
  if (typeof data?.access_token !== "string" || !data.access_token || typeof data.refresh_token !== "string" || !data.refresh_token) throw new SignInError(0);
  await prepare?.(data);
  await setToken(data.access_token, data.role === "tester" ? "tester" : "driver", "account", data.refresh_token);
  requestCode(false);
  return data;
}

/** Sign in with Apple: the identity token, the raw nonce made for it (src/signin/apple.ts), the authorization code
 *  (the server revokes the sign-in with it when the account is deleted) and the name Apple gave, if any. */
export const signInApple = (c: AppleCredential, prepare?: (r: SignInResponse) => unknown) =>
  signIn(
    "/auth/apple",
    { identity_token: c.identityToken, nonce: c.rawNonce, authorization_code: c.authorizationCode ?? undefined, name: c.name ?? undefined, platform: signInPlatform() },
    prepare,
  );

/** Sign in with Google: the id token from Google's sheet (src/signin/google.ts). */
export const signInGoogle = (idToken: string, prepare?: (r: SignInResponse) => unknown) =>
  signIn("/auth/google", { id_token: idToken, platform: signInPlatform() }, prepare);

/**
 * Closes this phone's session on the server (the account's other phones stay signed in). The server takes any
 * token it signed, expired or not, and answers the same when the session is already gone. False when it could not
 * be reached: the caller signs out locally all the same, and the session ends when it goes idle. Not through
 * request(): a 401 here must not start a renewal.
 */
export async function logout(): Promise<boolean> {
  const token = getToken();
  if (!token) return true;
  const d = deadline(15000);
  try {
    const res = await fetch(`${BASE}/auth/logout`, { method: "POST", headers: { Authorization: `Bearer ${token}` }, signal: d.signal });
    return res.ok;
  } catch {
    return false;
  } finally {
    d.done();
  }
}

/**
 * "Delete my data" on the server (DELETE /api/memory). Not through request(): the caller holds every other request
 * until this one settles (firstLaunch.ts deleteMyData), and request()'s renewal on a 401 waits on that very hold, so
 * an expired token hung the delete for good. The header is read in the same tick as the call, before the hold goes
 * up. A 401 comes back as an ApiError: the caller renews outside the hold and tries once more.
 */
export async function wipeServerData(): Promise<{ deleted: number }> {
  const d = deadline(DEFAULT_TIMEOUT_MS);
  try {
    const res = await fetchTracked(`${BASE}/memory`, { method: "DELETE", signal: d.signal, headers: { "Content-Type": "application/json", ...authHeader() } });
    await check(res, "/memory", null, false);
    return await json<{ deleted: number }>(res, "/memory");
  } finally {
    d.done();
  }
}

/** Open sign-up: a device token without a code. Not part of the launch flow since sign-in became required
 *  (src/components/SignInGate.tsx), and a "device" identity is now treated as signed out (firstLaunch.ts
 *  countsAsSignedIn). Kept for the fallback in the plan (an anonymous "Not now"), which would have to lift that rule. */
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
  const data = (await res.json()) as { access_token: string; refresh_token?: string };
  await setToken(data.access_token, "driver", "device", data.refresh_token ?? null);
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
  const data = await json<{ access_token: string; refresh_token?: string; role?: "driver" | "tester" }>(res, "/auth");
  await setToken(data.access_token, data.role === "tester" ? "tester" : "driver", "code", data.refresh_token ?? null);
  requestCode(false);
}

export const api = {
  memory: () => request<{ turns: MemoryTurn[] }>(`/memory`),
  clearMemory: () => request<{ deleted: number }>(`/memory`, { method: "DELETE" }),
  places: (category: Category, at: LatLng) =>
    request<PlacesResponse>(`/places?category=${category}&lat=${at.lat}&lng=${at.lng}`),
  /** POST /api/route; `via` = the stops already on the way, so a re-plan (new truck size, entrance,
   * off-route fallback) keeps them instead of silently routing straight to the destination. */
  route: async (from: LatLng, place: Place, skipParking = false, via: [number, number][] = [], tripId: string | null = null) =>
    request<RouteResult>(`/route`, {
      method: "POST",
      body: JSON.stringify({
        lat: from.lat,
        lng: from.lng,
        place,
        skip_parking: skipParking,
        via,
        trip_id: tripId ?? undefined,
        truck_profile: await ensureTruckProfile(),
      }),
    }).then((r) => checkContract(RouteResultSchema, r, "/route")),
  /** One batch of trip events (src/net/tripEvents.ts); 15 s is plenty, nothing waits on it. */
  tripEvents: (events: { t: string; trip_id?: string; kind: string; data: Record<string, unknown> }[]) =>
    request<{ stored: number }>(`/trips/events`, { method: "POST", body: JSON.stringify(events), timeoutMs: 15000 }),
  /** The events of one of the driver's own trips, oldest first (tester screen). */
  tripEventsOf: (tripId: string) =>
    request<{ events: TripEventRow[] }>(`/trips/${encodeURIComponent(tripId)}/events`).then((r) => r.events),
  /** The driver's truck profile on the server (fleet-managed later); 404 until the backend has the endpoint. */
  /** The stored profile and whose it is: "vehicle" = a fleet's truck (a driver's change holds only for the trip,
   *  on the phone), "driver" = the driver's own copy. Older servers send no header: "driver". */
  profile: async (): Promise<{ profile: TruckProfile; source: "vehicle" | "driver" }> => {
    const d = deadline(DEFAULT_TIMEOUT_MS);
    try {
      const sent = await heldToken();
      const who = identityEpoch();
      const res = await fetchTracked(`${BASE}/me/profile`, { signal: d.signal, headers: { "Content-Type": "application/json", ...authHeader() } });
      await check(res, "/me/profile", sent);
      stillSameDriver(who, "/me/profile");
      const source = res.headers.get("x-profile-source") === "vehicle" ? "vehicle" : "driver";
      return { profile: await json<TruckProfile>(res, "/me/profile"), source };
    } finally {
      d.done();
    }
  },
  saveProfile: (profile: TruckProfile) => request<TruckProfile>(`/me/profile`, { method: "PUT", body: JSON.stringify(profile) }),
  /**
   * Off-route reroute: the engine resumes the server-side plan (`handle`, section, metres into it)
   * instead of planning from scratch. Same shape back as /route, with a new `handle`.
   * 503 = routing down, 422 = outside the US; the caller falls back to /route on any failure.
   * 15 s: longer than that and the driver has long passed the next turn anyway.
   */
  reroute: async (body: RerouteBody) =>
    request<RouteResult>(`/reroute`, {
      method: "POST",
      timeoutMs: 15000,
      body: JSON.stringify({ ...body, truck_profile: await ensureTruckProfile() }),
    }).then((r) => checkContract(RouteResultSchema, r, "/reroute")),
  parking: (place: Place, approach?: Approach | null) =>
    request<ParkingCheck>(`/parking`, { method: "POST", body: JSON.stringify({ place, approach }), timeoutMs: 60000 }),
  /** Spoken parking answer; waits up to `waitS` for a satellite check in flight. */
  parkingAnswer: (place: Place, waitS: number) =>
    request<{ status: "ready" | "pending"; reply: string; audio_url?: string | null; parking?: ParkingCheck | null }>(
      `/parking/answer`,
      { method: "POST", body: JSON.stringify({ place, wait_s: waitS }), timeoutMs: waitS * 1000 + 30000 },
    ),
  agent: async (text: string, at: LatLng) =>
    request<AgentResponse>(`/agent`, {
      method: "POST",
      timeoutMs: 65000,
      headers: eldHeader(), // Motive key: the copilot reads hours of service
      body: JSON.stringify({ text, lat: at.lat, lng: at.lng, truck_profile: await ensureTruckProfile() }),
    }),
  /** `timeoutMs`: the speech queue gives a line a few seconds, not the default 30 (a turn call cannot wait). */
  speak: (text: string, timeoutMs?: number) =>
    request<{ audio_url: string }>(`/speak`, { method: "POST", body: JSON.stringify({ text }), timeoutMs }),
  /** Pre-fetch nearby places (+ parking checks) so the next voice turn hits warm caches. */
  warm: (at: LatLng) => request<{ ok: boolean }>(`/warm?lat=${at.lat}&lng=${at.lng}`, { method: "POST" }),
  /**
   * Voice turn step 1: speech -> text, shown to the driver right away. `stt_ms` is the server's
   * STT stage when it reports one, else the wall time of this call; agentSpeak forwards it along
   * with `stt_confidence` (null when the recogniser reports none). `hint` = names to expect,
   * comma-separated (voice/sttHint.ts); the server takes at most 600 chars.
   */
  /** `maybe`: the mic heard something but no clear speech (src/voice/endpointer.ts); the server returns an empty
   *  transcript for a short low-confidence guess instead of a made-up "Thank you." */
  transcribe: async (uri: string, signal?: AbortSignal, hint?: string, maybe = false) => {
    const web = uri.startsWith("blob:") || uri.startsWith("data:");
    const d = deadline(20000, signal); // upload + STT; the server itself gives up on STT after 12 s
    const t0 = Date.now();
    try {
      const sent = await heldToken();
      const who = identityEpoch();
      const res = await uploadForm(`${BASE}/voice/transcribe`, {
        fields: { ...(hint ? { hint: hint.slice(0, 600) } : {}), ...(maybe ? { maybe: "1" } : {}) },
        files: { audio: { uri, name: web ? "speech.webm" : "speech.m4a", mimeType: web ? "audio/webm" : "audio/mp4" } },
        headers: { ...authHeader(), ...eldHeader() },
        signal: d.signal,
      });
      await check(res as Response, "/voice/transcribe", sent);
      stillSameDriver(who, "/voice/transcribe");
      const out = await json<{ transcript: string; timings?: Record<string, number>; stt_confidence?: number | null }>(res, "/voice/transcribe");
      return { ...out, stt_ms: out.timings?.stt ?? Date.now() - t0, stt_confidence: out.stt_confidence ?? null };
    } finally {
      d.done();
    }
  },
  /** Voice turn step 2: text -> action + spoken reply. */
  agentSpeak: async (
    text: string,
    at: LatLng,
    active?: ActiveRoute | null,
    signal?: AbortSignal,
    sessionId?: string,
    ui?: UiState,
    heading: number | null = null, // degrees from the GPS fix, null when the truck is standing still
    activeSpeedLimitMph: number | null = null, // posted limit where the truck is, when the route knows it
    sttMs: number | null = null, // how long speech-to-text took on this turn (server stage, else measured); null for typed text
    sttConfidence: number | null = null, // 0..1 from /voice/transcribe; null for typed text or when the recogniser gives none
    speedMps: number | null = null, // GPS speed in m/s as the location hook reports it; left out of the body when unknown
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
        active_speed_limit_mph: activeSpeedLimitMph,
        truck_profile: await ensureTruckProfile(),
        session_id: sessionId,
        active_place: active?.place,
        // The route as it is, so the copilot can check chains or other ways on it after a restart (its session
        // cache is gone then): HERE handle and the stops still ahead.
        route_handle: active?.route.handle ?? null,
        route_via: active ? viaOf(stopsAhead(active.route, active.stops, at)) : [],
        // The same stops as places: the trip lives on the phone, and the copilot names, adds and drops stops by
        // what they are (after a restart its session knew only coordinates).
        route_stops: active ? stopsAhead(active.route, active.stops, at) : [],
        active_distance_mi: active?.route.distance_mi,
        active_duration_min: active?.route.duration_min,
        trip_id: active?.trip_id,
        ui,
        stt_ms: sttMs,
        stt_confidence: sttConfidence,
        speed_mps: speedForAgent(speedMps),
      }),
    }).then((r) => checkContract(AgentResponseSchema, r, "/agent")),
  /**
   * "Anything to say?" while driving (every few minutes): break and parking nudges, spoken as
   * ambient lines. The backend keeps an offer pending for the next turn of the same session.
   */
  nudges: async (body: NudgeRequest, signal?: AbortSignal) =>
    request<{ nudges: Nudge[] }>(`/nudges`, {
      method: "POST",
      signal,
      timeoutMs: 20000,
      headers: eldHeader(), // Motive key: hours of service decide when a break is due
      body: JSON.stringify({ ...body, truck_profile: await ensureTruckProfile() }),
    }),
  /** Breakdown research result (long-poll: waits until the research is done). */
  breakdownResult: (id: string, signal?: AbortSignal) =>
    request<AgentResponse>(`/breakdown/${id}?speak=true`, { signal, timeoutMs: 120000 }),
  onboardingParse: async (uri: string, step: "name" | "work" | "truck" | "prefs") => {
    const web = uri.startsWith("blob:") || uri.startsWith("data:");
    const d = deadline(30000);
    try {
      const sent = await heldToken();
      const who = identityEpoch();
      const res = await uploadForm(`${BASE}/onboarding/parse`, {
        fields: { step },
        files: { audio: { uri, name: web ? "speech.webm" : "speech.m4a", mimeType: web ? "audio/webm" : "audio/mp4" } },
        headers: authHeader(),
        signal: d.signal,
      });
      await check(res as Response, "/onboarding/parse", sent);
      stillSameDriver(who, "/onboarding/parse");
      return json<{ transcript: string; updates: Record<string, unknown> }>(res, "/onboarding/parse");
    } finally {
      d.done();
    }
  },
};
