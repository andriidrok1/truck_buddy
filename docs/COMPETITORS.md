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

## Trucker Path as a company (the benchmark)

Phoenix, founded 2013, owned by Moatable Inc. (NYSE: MTBL, formerly Renren) since Dec 2017, ~95 staff. Public company, so the numbers are real (Moatable 10-Q, Q3 2025):

| | Q3 2025 | YoY |
|---|---|---|
| Trucker Path revenue | $10.3M | +39% |
| Paying subscriptions | ~152k | +30% |
| Gross margin | 70% | |

Run-rate about $41M/yr. With 1M+ MAU, roughly 15% pay. Revenue per paying sub is $22.6/mo, but that includes the load board and fleet seats; a single driver typically pays the annual Gold rate, $12.50/mo.

Product lines and what each costs:

1. Driver app. Free with ads: map, crowd check-ins for parking and weigh stations (100M+/mo), diesel prices, reviews. Gold $9.99–19.99/mo or $149.99/yr: navigation, no ads. Diamond $29.99/mo or $249.99/yr, 7-day trial: parking predictions, offline maps, multi-stop, CarPlay, Via/Avoid, 511 and weather. Parking reservations since June 2026: 50k+ spots, ~$20 average per booking, partner Realize Mobility. Diesel discounts via 10-4 by WEX, free to the driver.
2. TruckLoads load board. Search is free. Starter $29.99/mo ($239.88/yr), Professional $49.99/mo ($399.99/yr): broker credit score and days-to-pay, 1/7/15-day rate check, AI load search, document scanner (rate con, BOL → Excel), and voice requests for parking and cheapest diesel on a route. 150k loads/day, 800+ brokers, 100k carriers.
3. Fleets (Nav Pro). Individual Driver $29.99/mo (1–2 trucks), Starter $79/mo (1 operator + 3 drivers, extra operator $50, driver $15), Business $199/mo (1 + 11, driver $12), 101+ on request, 7-day trial. 2,800+ fleets, 45k vehicles, 30+ ELDs, McLeod, a 36-endpoint API. Fleet Fuel and Reserved Parking sold separately.
4. ELD via Geotab hardware; no public price any more (once "free hardware, from $8.30/mo"); ELD compliance only from the Starter fleet tier.
5. Insurance agency (2025): auto liability, physical damage, cargo, NTL, workers' comp; ~$7M book of premiums. Deals: partner offers (ATBS, Lendio, pharmacy card, telehealth, Realize parking).

Dead: InstaPay factoring, sold to Flexible Funding in 2018.

What it means for us:

- The free tier is the funnel: 1M MAU, 15% convert. We need a free mode capped by voice minutes per day, not a paywall at the door.
- Voice is a premium feature inside their paid load board. For us voice is the base; say so on the store page.
- Parking reservations at ~$20 with a cut is their first per-transaction revenue in the driver app. Same move for us through Realize or TruckParkingClub, no infrastructure of our own.
- $12.50/mo is the ceiling for "another truck app". A $14.99 tier has to sit next to the Diamond feature set, not Gold.
- Subscriptions growing 30%/yr means drivers pay for software.

They have no conversational interface, no satellite interpretation, no breakdown triage with providers. Their moat is the check-ins; we don't replicate it, we go around it with state data (TPIMS) and chain data (Love's).

Sources: truckerpath.com (pricing, fleet, truckloads, deals, insurance), App Store id782746890, Wikipedia, yespress.io/trucker-path, Moatable 10-Q Q3 2025, ttnews.com and thetrucker.com (parking reservations, June 2026), trucknews.com (Acapela voices), tech.co (ELD).

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

## How to close each gap (researched 2026-10-27)

Every item below was checked against the live endpoint or the official docs on 2026-10-27. "Unverified" means we could not reach the source. Full Russian write-up with per-state details lives in the vault.

### Budget

- HERE: 1 request = 1 transaction; `via` and `alternatives` do not multiply it. Free tier (third-party figure, confirm in platform.here.com): Routing ~5,000/mo, Search 30,000, Traffic ~5,000, Waypoints Sequence 500. Overage ~$2.80 per 1,000 truck routes. One driver-day is 25–35 routing transactions (route, 0–10 reroutes, incident refresh every 30 min), so 5,000/mo covers ~150 driver-days.
- Google Places: `reviews`, `fuelOptions`, `generativeSummary` are in the Enterprise + Atmosphere SKU we already pay for via `editorialSummary`. Adding them to the field mask costs $0.

### Routing (HERE, key in hand)

| Gap | How | Effort |
|---|---|---|
| Re-routing off route | Request `return=routeHandle`; on deviation call `GET /v8/routes/{handle}?origin=lat,lng;course=N&rerouting[mode]=returnToRoute&rerouting[lastTraveledSectionIndex]=N&rerouting[traveledDistanceOnLastSection]=m`; fall back to a fresh `/v8/routes` if the handle expired. Client detects: >50 m off the polyline (30 m in town) for 3 fixes, 30 s cooldown. Backend: `RouteInfo.route_handle`, `routing.reroute()`, `POST /api/reroute`. | 2 d + 1 d app |
| Stop on the way (Via) | `via=lat,lng!stopDuration=1800`, key repeated per stop, truck params stay global. Agent intent `add_stop` → geocode → route with via; keep the via list in the session for reroutes. | 1–2 d |
| Avoid | Already: tolls, ferries, tunnels. Add `controlledAccessHighway`, `dirtRoad`; `avoid[areas]=bbox` for "avoid downtown X"; `avoid[segments]` for "not this road" (needs `spans=segmentRef`). HERE warns truck + tollRoad on long routes can return nothing. | 1–2 d |
| Alternatives | `alternatives=1..6`, `return=routeLabels,tolls&currency=USD`. One transaction. "Another way?" answered from cache. | 1–2 d |
| Speed limit | `spans=maxSpeed` in the same call (`speedLimit` is deprecated), $0. Lane guidance exists only in the HERE Navigate SDK: skip. | 0.5–1 d |
| Multi-stop | ≤10 stops in known order: plain via. Unknown order: Waypoints Sequence `findsequence2` with truck params, then one `/v8/routes` with via in that order. | 2–3 d |
| Fuel ahead on the route | HERE Search `/browse?route={flexpolyline};w=2000&categories=700-7600-0116&ranking=excursionDistance` (≤300 points in GET, Search bucket). Prices from HERE only by contract (Fuel Prices v3 corridor). Google `searchAlongRouteParameters` + `fuelOptions` at $40/1,000 only when the driver asks for prices. | 1–2 d |
| Closures and truck restrictions | `return=incidents&spans=incidents` in the routing call ($0): `roadClosure`, `construction`, `criticality`. Mid-trip refresh through the handle every 30 min. Traffic v7 `/incidents?in=corridor:{flexpolyline};r=500` adds `vehicleRestrictions[]` (height/weight) for trucks. | 1 d |

### Public data (free, no contracts)

| Gap | Source | Effort |
|---|---|---|
| Live parking counts | TPIMS (MAASTO v2.2 JSON: `siteId, reportedAvailable, trend, open, capacity`, every 1–5 min). Open today without a key: WI `https://511wi.gov/api/TPIMS_Dynamic` (14 lots), KY `trimarc.org/dat/tpims/TPIMS_Dynamic.json` (13), IL `truckparking.travelmidwest.com/TPIMS_Dynamic.json` (51, all "Unknown" today). Keyed: KS (tpims.ksdot.gov), IA and MI by email, NY (511ny, 10 calls/min). IN/OH gone. TX I-10 and FL: unverified. Capacity nationwide: FHWA NTAD "Truck Stop Parking" ArcGIS FeatureServer, 1,915 public sites, 39,803 spaces, public domain. `services/truck_parking.py` polls every 2 min, joins to Google places within 300 m. Spoken: "Portage rest area, exit 113, 56 of 68 open, clearing." | 2 d + 1 d per keyed state |
| Weigh station locations | OSM `amenity=weighbridge` filtered by name `Weigh|Inspection|Port of Entry|Scales` (else grain elevators); Caltrans CVEF ArcGIS layer for CA. One-off Overpass export into the curated list, category `weigh_station`. Open/closed status: no free source anywhere. | 1 d |
| Chain controls, 511 | HERE corridor above for the nation. Caltrans CWWP2 `cwwp2.dot.ca.gov/data/d3/cc/ccStatusD03.json` (per district, every minute, R-0..R-3, public domain, no key). WSDOT by key: `CVRestrictions` and mountain passes. NV/UT/AZ/ID/NY run the IBI 511 stack, key, 10 calls/min. Aggregator fallback: road511.com from $29/mo. `services/hazards.py`. | 2 d + 1 d Caltrans + 1 d WSDOT |
| Weather on the route | NWS `api.weather.gov`, free, `User-Agent` with contact required. `/alerts/active?point=lat,lng` sampled every ~40 km, or one `?area=ST` per state and a local polygon test. Filter: High Wind, Winter Storm, Blizzard, Ice Storm, Snow Squall, Dense/Freezing Fog, Dust Storm, Flash Flood, Extreme Cold, Tornado. Hourly: `/points/{lat},{lon}` → `forecastHourly`. `services/weather.py`. | 1.5 d |
| Walmart overnight | No dataset, Walmart does not track it. Google text query "Walmart Supercenter", then our satellite lot check, spoken with "overnight depends on the manager". | 0.5 d |
| CAT Scale | Official CSV `catscale.com/exl.php`, 2,353 rows (Love's 571, Pilot 441, TA 210). Terms say internal use: email CAT for permission before shipping; fallback Google query "CAT Scale". | 0.5 d |
| Truck wash | Google "truck wash" with `includedType=car_wash` and a name filter; Blue Beacon has 117 sites and no API. | 0.5 d |
| DEF | No source. Rule: every Love's / Pilot / Flying J / TA / Petro has DEF at the diesel islands (match via `TRUCK_STOP_NAME`), independents "unknown". | 0.25 d |
| Rest-area space counts | NTAD above: 856 rest areas and 94 welcome centers with `number_of_spots`. | in the TPIMS work |

### Partners (no public API, needs a conversation)

- Parking reservations: Realize (30+ lots, web booking only), Truck Parking Club (4,000+ sites, instant booking, gave an API to Hey Bubba, a voice dispatcher: our precedent), SecurSpace (owner approval ~30 min), Relay Parking. Fastest: email TPC citing Hey Bubba, ask for API + referral code. Until then, deep-link to their location page. 1–2 d after credentials.
- Live occupancy from chains: Pilot shows it in its app, API only for fleets by contract; Love's shows a count only; TA by phone. TPIMS is the only live source available today.
- Diesel prices: Love's developer portal has "Store & Fuel Prices" (`/stores`, `/fuelPrices?products=20,21,22`, OAuth client credentials), access via Request Access or a rep, expected $0. Pilot: none. GasBuddy: enterprise contract. OPIS: five figures a year. Mudflap: OAuth server exists, no OpenAPI. Free and working: EIA `api.eia.gov/v2/petroleum/pri/gnd/data` weekly by PADD/state (Gulf Coast $5.951 on 2026-10-26) for "you're 30 cents above the regional average". 2 h.
- Google reviews: `places.reviews` in our SKU, max 5, $0. Show author (avatar minimum) and `googleMapsUri` on screen, do not store text past the request. No rule against a spoken paraphrase. Gemini `reviewSummary` is UK/India/Japan only, so we summarise with our own LLM. 0.5 d.
- Fuel discounts: 10-4 by WEX has no partner program or API; Mudflap only in-app referral codes; TCS Fuel Card has a referral link. Realistic: "I can text you a link", $0–100 per signup. 1 h.
- HOS from ELDs: Motive `GET /v1/available_time` (drive/shift/cycle/break seconds), the driver pastes a personal API key from their own dashboard, no approval needed. Samsara `GET /fleet/hos/clocks` via OAuth, cleaner but fleet-skewed. Geotab rare for owner-operators. "Connect ELD" screen, 60 s cache. 1 d per provider.
- Drivewyze: no API or feed, referral partner only. Skip.

### Platform

- CarPlay: `react-native-carplay` is dead (last release June 2024, crashes on Expo 54). Use `@iternio/react-native-auto-play` 0.9.5 (2026-10-23, MIT, New Architecture, CarPlay + Android Auto, `startVoiceInput()` with CPVoiceControlTemplate). No config plugin: write our own (Info.plist scene manifest, entitlements, AppDelegate hook), ~1.5 d. Category: iOS 26.4 added "voice-based conversational" (`carplay-voice-based-conversation`), which is tap-to-talk on the car screen; ChatGPT ships on it. Apple is unlikely to grant the navigation entitlement without our own map rendering. Apply at developer.apple.com/contact/carplay now; wait is 2 weeks to 4+ months, no escalation. Dev build only. 5–7 d code + 2 d in a car. Audio note without CarPlay: expo-audio `allowsRecording: true` flips the session to playAndRecord and Bluetooth drops to HFP (8 kHz); enable recording only while capturing, disable before TTS.
- Android: $25 account; personal accounts must run a closed test with 12 testers for 14 consecutive days before production. Needs `android.config.googleMaps.apiKey` (map is blank without it), an `expo-symbols` fallback, Picovoice foreground service removed (done in PR #7). Data safety: location + voice recordings. Maps SDK for Android has no usage cap but billing must be on. EAS Free: 15 Android builds/mo. 3–4 d + 1 d forms + 14 d testers.
- Wake word: Picovoice free-tier keys died 2026-06-30 and the SDK checks the key at init. `react-native-voice-activator` 0.2.0 (2026-10-17, MIT) spots any two-word phrase without training (sherpa-onnx + Silero VAD, 7.8 MB model, dev build), accuracy unmeasured on devices. SFSpeechRecognizer cannot run always-on (1-minute cap). Plan: on-device STT via `expo-speech-recognition` 58.0.0 first (works in dead zones, 2 d), then voice-activator foreground-only behind a flag with a week of false-trigger measurement in a cab (5 d). Do not promise background "Hey Buddy" on iOS.
- TTS cost: OpenAI tts-1 $15/1M chars; gpt-4o-mini-tts costs about the same per minute with a better voice, switch. 50 answers × 200 chars/day ≈ $4.50 per driver per month. `expo-speech` is free and offline but only Compact voices unless the user downloads Enhanced in Settings. Hybrid: expo-speech for short system phrases and offline mode, bundled MP3s for fixed phrases, OpenAI for conversational answers, an "offline voice" toggle. 1.5 d.

## Order of work

Week 1 ($0, keys in hand): HERE incidents + `spans=maxSpeed` (1 d), NWS weather (1.5 d), reviews + fuelOptions + EIA (1 d), CAT Scale / Walmart / truck wash / DEF (1.5 d). Send the same day: CarPlay voice-category request, Love's Request Access, TPC email citing Hey Bubba, CAT CSV permission.

Week 2: re-routing + via + alternatives (4–5 d), NTAD + TPIMS WI/KY/IL (2 d).

Week 3: Caltrans chains + WSDOT (2 d), HOS via Motive (1 d), on-device STT (2 d), TTS hybrid (1.5 d), Android bring-up (3–4 d) and recruit 12 testers.

After: CarPlay once the entitlement lands (7–9 d), multi-stop via Waypoints Sequence, wake word experiment, parking reservations after TPC replies.

Not doing: lane guidance (SDK only), weigh-station open/closed status (no source), OPIS/GasBuddy, Drivewyze, Pilot API, offline maps.

Unverified: exact HERE free-tier limits (plans page unreachable), US count of OSM weigh stations (Overpass down), TPIMS feeds for MN/TX/FL, Walmart's Google place type.
