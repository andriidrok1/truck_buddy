# Competitors: what they sell, what they charge, what we still lack

A running checklist. Tick a box when the feature ships in TruckBuddy (link the PR). Re-check prices every quarter; the "Checked" date is when someone last looked at the source.

Legend: `[x]` we have it · `[ ]` we don't · `[-]` we are not going to copy it (reason given).

## Pricing we compete against

| Product | Price | Free tier | Checked |
|---|---|---|---|
| Trucker Path (iOS, ~1M MAU, 4.8 / 156k ratings) | Gold $9.99–19.99/mo, Diamond $29.99/mo or $149.99 / 6 mo; fleet "Individual Driver" $29.99/mo, Starter $79/mo (3–10 trucks), Business $199/mo (11–50) | Yes, with ads: map, parking/weigh-station status, diesel prices, reviews, load board | 2026-10-27 |
| Hammer | Free, optional donation $3–5 | Everything | 2026-10-27 |
| CoPilot Truck (Trimble) | Subscription | Trial only | 2026-10-27 |
| Sygic Truck & Caravan | Freemium + subscription | Map, basic | 2026-10-27 |
| Garmin dēzl (hardware) | $350–700 one-off | n/a | 2026-10-27 |
| Truckstop AVA (voice, loads) | Inside the Truckstop load-board subscription | No | 2026-10-27 |
| Hey Bubba / DispatchMVP Otto (voice, loads) | Free start, paid tiers | Yes | 2026-10-27 |
| TruckParkingClub | Per reservation | Browsing | 2026-10-27 |
| Mudflap (fuel discounts) | Free, commission from fuel stops | Everything | 2026-10-27 |
| TruckBuddy | Free during early access; paid tier via In-App Purchase later | — | — |

Apple keeps 15% under the Small Business Program, so a $14.99 tier nets $12.74 per driver per month. Our per-session cost (find → route → parking) is about $0.12; a chat-heavy driver at the 10-min/day cap costs up to $15–18/mo (see the unit-economics note).

## Trucker Path

Source: truckerpath.com (pricing, compare, trucker-path-app, truck-gps, faq) and App Store id782746890, version 7.1.7 (2026-10-10).

### Free in Trucker Path

- [x] Truck routing by height/length/weight/hazmat — HERE truck routing (`backend/services/routing.py`).
- [x] Truck stops with amenities — Google Places + curated list (`backend/services/google_places.py`).
- [x] Hotels with truck lots, rest areas — categories `hotel`, `rest_area`.
- [x] Diesel prices (they list 5,000+ stations) — Google `fuelOptions` on truck stops, truck-lane price preferred (PR #7). Coverage unverified until a Google key is in place.
- [x] Favorite chains — `favorite_chains` in the truck profile, +5 mi bias when picking.
- [x] Night mode — dark theme only, no toggle.
- [ ] Weigh stations: locations. We have none. Plan: OSM `amenity=weighbridge` / `highway=weigh_station` via Overpass, refreshed monthly into the curated list; spoken "weigh station in 5 miles" along the route.
- [-] Weigh stations: open/closed status. Crowd-sourced from 1M drivers; nothing to source it from.
- [ ] Parking availability: real counts. We have the satellite verdict (lot size, entrance) but no live counts. Plan: TPIMS feeds (public 511 APIs in IA, KS, KY, MI, MN, OH, WI along I-80/I-94, ~150 lots, free). Later: TruckParkingClub partnership.
- [-] Parking availability: crowd check-ins. Same as weigh-station status.
- [ ] Walmart stores with overnight parking. Plan: Google Places "Walmart Supercenter" as a category; overnight policy per store is crowd data at TP, we can only say "ask the store".
- [ ] CAT Scale locations. Plan: category + Google text query "CAT Scale".
- [ ] Truck wash (they list 400+, Blue Beacon). Plan: category + Google text query.
- [ ] DEF availability at fuel stops. Plan: Google `fuelOptions` does not carry DEF; needs Love's/Pilot data.
- [ ] Driver reviews and ratings. Plan: `places.reviews` is already in the Google SKU we pay for (Enterprise + Atmosphere), so a spoken digest ("drivers say the showers are clean") costs nothing extra.
- [ ] Favorite places (saved spots). We save chains, not places. Plan: "save this spot" into driver memory.
- [-] DOT inspection locations. Not our wedge.
- [-] Load board (150k loads/day, AI doc scanner, reply to brokers with AI). Different product; revisit only with the "call the broker" idea.
- [-] Job search, insurance. Not our wedge.

### Paid in Trucker Path (Gold / Diamond)

- [x] Turn-by-turn navigation — HERE maneuvers, `NavBanner`, spoken.
- [ ] Re-routing when the driver leaves the route. `models.py` says "no re-routing per turn". Plan: one HERE call when the position is >150 m off the line, debounced.
- [ ] Route editing: Via and Avoid. Plan: HERE `via=` and `avoid[features]` (tolls/ferries already wired), by voice: "stop at Love's in Lodi on the way".
- [ ] Route comparison / alternatives. Plan: HERE `alternatives=2`, spoken as "the I-5 way is 20 minutes shorter, the 99 skips the grade".
- [ ] Multi-stop trips (TP paywalls >3 stops). Plan: same `via=` work.
- [ ] Multi-stop fuel planning ("cheapest diesel on my route"). Plan: Text Search along the route corridor (3–5 Google calls, $0.12–0.20 per plan) now; Love's API later makes it free.
- [ ] Parking predictions. Plan: none until we have TPIMS or partner data.
- [ ] Live traffic cameras and 511 alerts along the route. Plan: state 511 APIs (Caltrans QuickMap first) for closures and chain controls; cameras are useless by voice, skip those.
- [ ] Weather alerts along the route. Plan: NWS alerts API by route polygon, free; spoken "high wind warning on I-80 over Donner".
- [x] Live traffic on the route — HERE traffic spans drawn on the map.
- [ ] HOS rest recommendations in the trip planner. We have break reminders by hours (`set_break_hours`). Plan: tie them to the route geometry: "you'll hit 8 hours near Reno, there's a Pilot there".
- [ ] Apple CarPlay. Plan: `react-native-carplay` in a dev build plus Apple's CarPlay navigation entitlement (weeks of review). Target 1.2.
- [-] Offline maps. Everything we do needs the server (LLM, STT, places). A different product; don't chase.
- [-] Ad-free. We have no ads.

### Fuel discounts (free, they earn on the card)

- [-] 10-4 by WEX diesel discounts paid in-app. Needs a card-issuer partnership. Revisit after Love's/Pilot talks.

## Hammer

- [-] Offline maps. See above.
- [ ] Fuel prices along the route. Same item as multi-stop fuel planning.
- [ ] Re-routing on traffic. Same item as re-routing.

## CoPilot Truck, Sygic Truck

- [-] Offline-first routing. See above.
- [ ] Android. Expo makes it cheap; blocked on Google Play account and a Play-side review of the mic permission text.
- [ ] Lane guidance and speed limits. HERE routing returns neither in our current call; HERE has speed limits in the `spans` response (`speedLimit` attribute), lane guidance needs the navigation SDK. Low priority for voice.

## Garmin dēzl

- [x] Satellite look at the destination lot (BirdsEye) — our parking check interprets Esri imagery and marks the entrance.
- [x] Service directory (repair, tires, towing) — breakdown flow with providers, ratings, phone numbers.
- [-] Works without a phone. Hardware.

## Truckstop AVA, Hey Bubba, DispatchMVP Otto (voice for loads)

- [-] Book loads, negotiate rates, call the broker by voice. Not in 1.x; the VibeCon "call the broker" idea lives in the vault, not here.
- [ ] Voice status updates to the dispatcher ("I'm 40 minutes out"). Cheap once we have a share sheet; low priority.

## TruckParkingClub

- [ ] Reserve a parking spot. Plan: partnership or deep link to their booking page from our parking card.
- [ ] Live occupancy in 8 states. Same as TPIMS item; theirs is private data.

## Mudflap

- [-] Diesel discounts. Same as WEX.

## What we have and none of them sell

- Conversational search by meaning: "a hotel with a lot", "tire replacement on the highway" with providers and phone numbers.
- Satellite interpretation of the lot: where the entrance is, where trailers park, what to avoid.
- Truck profile applied to routing with no hardware.
- A copilot you talk to; every competitor is a map you tap.

## Order of work

1.1 (existing keys, ~2 weeks): re-routing, via/alternatives by voice, weather + 511 closures, weigh-station locations with a spoken warning, CAT Scale / truck wash / Walmart categories, Google reviews digest, HOS tied to the route, saved places.

1.2: TPIMS parking counts, fuel along the corridor (after Love's), CarPlay, Android.

Not copying: offline maps, community check-ins, load board, WEX card.
