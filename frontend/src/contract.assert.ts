// Type-level check that the hand-written types in src/types.ts still describe the backend's schema
// (src/types.generated.ts from contracts/openapi.json). Fails `tsc` when a field the app relies on changes
// shape on the server. Nothing here runs.
import type { components } from "./types.generated";
import type { AgentResponse, AppAction, Maneuver, Place, RouteInfo } from "./types";

type Gen = components["schemas"];
/** The server's type must fit what the app expects (the app may be stricter, never looser). */
type Fits<Server, App> = [Server] extends [App] ? true : false;
type Assert<T extends true> = T;

export type _AgentStatus = Assert<Fits<Gen["AgentResponse"]["status"], AgentResponse["status"]>>;
export type _AgentReply = Assert<Fits<Gen["AgentResponse"]["reply"], AgentResponse["reply"]>>;
export type _ActionType = Assert<Fits<Gen["AppAction"]["type"], AppAction["type"]>>;
export type _PlaceCore = Assert<Fits<Pick<Gen["Place"], "id" | "name" | "lat" | "lng">, Pick<Place, "id" | "name" | "lat" | "lng">>>;
export type _RouteCore = Assert<Fits<Pick<Gen["RouteInfo"], "distance_m" | "duration_s" | "summary">, Pick<RouteInfo, "distance_m" | "duration_s" | "summary">>>;
// geometry: the server says number[][], the app narrows it to [lat, lng] pairs; only the element type is checked
export type _Geometry = Assert<Fits<RouteInfo["geometry"][number], Gen["RouteInfo"]["geometry"][number]>>;
export type _Maneuver = Assert<Fits<Pick<Gen["Maneuver"], "action" | "instruction" | "index">, Pick<Maneuver, "action" | "instruction" | "index">>>;
