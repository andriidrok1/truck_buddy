import { arrivals, loadArrived, saveArrived } from "../geo/arrival";
import type { Place } from "../types";

const place = (id: string, lat: number, lng: number) => ({ id, name: id, category: "truck_stop", lat, lng }) as Place;
const pilot = place("pilot", 38.6, -121.5);
const reno = place("reno", 39.5296, -119.8138);

test("a stop is reached once, when the truck stands at it", () => {
  const seen = new Set<string>();
  expect(arrivals({ lat: 38.6005, lng: -121.5, speed: 25 }, [pilot], reno, seen)).toEqual([]); // passing at speed
  expect(arrivals({ lat: 38.6005, lng: -121.5, speed: 0.5 }, [pilot], reno, seen)).toEqual([{ place: pilot, kind: "stop" }]);
  expect(arrivals({ lat: 38.6005, lng: -121.5, speed: 0 }, [pilot], reno, seen)).toEqual([]); // once
});

test("the destination, and nothing far away", () => {
  const seen = new Set<string>();
  expect(arrivals({ lat: 38.7, lng: -121.5, speed: 0 }, [pilot], reno, seen)).toEqual([]);
  expect(arrivals({ lat: 39.5299, lng: -119.8138, speed: null }, [pilot], reno, seen)).toEqual([{ place: reno, kind: "destination" }]);
});

test("what was reported survives a restart of the same trip, and a new trip starts empty", async () => {
  const seen = new Set<string>();
  arrivals({ lat: 38.6005, lng: -121.5, speed: 0 }, [pilot], reno, seen);
  await saveArrived("trip-1", seen);
  const back = await loadArrived("trip-1"); // the app was killed and reopened
  expect(arrivals({ lat: 38.6005, lng: -121.5, speed: 0 }, [pilot], reno, back)).toEqual([]);
  expect([...(await loadArrived("trip-2"))]).toEqual([]);
});
