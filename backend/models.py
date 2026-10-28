from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field

# Searchable categories. The first three are the core; the rest are gated by CATEGORIES_EXTRA (see agent).
Category = Literal["truck_stop", "hotel", "rest_area", "walmart", "cat_scale", "truck_wash", "weigh_station"]
PlaceCategory = Literal["truck_stop", "hotel", "rest_area", "walmart", "cat_scale", "truck_wash", "weigh_station",
                        "destination", "service"]  # service = breakdown provider


class LatLng(BaseModel):
    lat: float
    lng: float


class Review(BaseModel):
    """One Google review. Google's terms: show the author and link to the review when displayed."""
    author: str
    rating: Optional[int] = None  # 1..5
    text: str  # capped for speech and the card
    uri: Optional[str] = None  # the review on Google Maps (googleMapsUri), else the author's profile
    time: Optional[str] = None  # RFC 3339 publishTime


class Place(BaseModel):
    id: str
    name: str
    category: PlaceCategory
    lat: float
    lng: float
    tags: List[str] = Field(default_factory=list)
    address: Optional[str] = None
    rating: Optional[float] = None
    rating_count: Optional[int] = None
    summary: Optional[str] = None
    diesel_price: Optional[float] = None  # USD per gallon, last known (Google fuelOptions)
    diesel_kind: Optional[Literal["truck", "auto"]] = None  # truck = TRUCK_DIESEL lane price, auto = car-pump diesel
    diesel_updated: Optional[str] = None  # RFC 3339, when the price was last reported
    distance_km: Optional[float] = None
    distance_mi: Optional[float] = None
    reviews: List[Review] = Field(default_factory=list)  # up to 5 (Google), for the card and a spoken gist


class PlacesResponse(BaseModel):
    category: Category
    radius_km: float
    widened: bool
    places: List[Place]
    source: str
    message: Optional[str] = None  # e.g. outside the demo coverage area


class TrafficSegment(BaseModel):
    level: Literal["slow", "jam"]  # slow = yellow, jam = red
    points: List[List[float]]  # [[lat, lng], ...]


class Maneuver(BaseModel):
    action: str  # depart | turn | arrive | continue | roundaboutExit | ...
    direction: Optional[str] = None  # left | right | middle
    instruction: str  # "Turn left onto J St"
    lat: float
    lng: float
    index: int  # point index in RouteInfo.geometry where the maneuver happens


class SpeedLimit(BaseModel):
    """Posted limit from `index` in RouteInfo.geometry until the next entry (HERE spans=maxSpeed)."""
    index: int
    mph: int


class Incident(BaseModel):
    """Traffic incident on the route (HERE return=incidents); `index` is where it starts in the geometry."""
    type: str  # roadClosure | construction | accident | congestion | weather | laneRestriction | ...
    closed: bool  # the road is closed (type roadClosure, or criticality critical)
    description: str  # short, already capped for speech
    index: int
    criticality: str = "minor"  # low | minor | major | critical


class WeatherAlert(BaseModel):
    """An active NWS alert on the route; `index` is the geometry index of the first sample point it covers."""
    id: str
    event: str  # "High Wind Warning", "Winter Storm Warning", ...
    severity: str  # Extreme | Severe | Moderate | Minor | Unknown
    headline: str
    area: str  # first part of NWS areaDesc, e.g. "Greater Lake Tahoe Area"
    index: int
    ends: Optional[str] = None  # RFC 3339


class ChainControl(BaseModel):
    """A Caltrans chain-control point; `index` is the geometry index of the route near it (−1 when unplaced)."""
    id: str
    route: str  # "I-80", "SR-89", "US-50"
    location: str  # "Luther Pass", "Kingvale"
    nearby: Optional[str] = None
    lat: float
    lng: float
    direction: Optional[str] = None
    status: str  # R-0 (none) | R-1 | R-2 | R-3
    description: str = ""
    updated: Optional[str] = None  # "2026-05-05 05:58:27" Pacific, as Caltrans publishes it
    in_service: bool = True
    index: int = -1


class Approach(BaseModel):
    """How the driver arrives: heading over the last 300 m and the street of the last maneuver."""
    bearing: int  # degrees, 0 = heading north
    compass: str  # side the driver comes FROM, e.g. "south"
    street: Optional[str] = None
    lat: Optional[float] = None  # approach point: route point 100 m before the finish
    lng: Optional[float] = None


class RouteInfo(BaseModel):
    geometry: List[List[float]]  # [[lat, lng], ...]
    distance_m: float
    duration_s: float
    distance_mi: float
    duration_min: int
    summary: str  # "12 min · 6.2 mi" / "5 h 20 min · 312 mi"
    source: str  # here | openrouteservice | mock
    notices: List[str] = Field(default_factory=list)  # restriction warnings from the router
    truck_aware: bool = False  # True when height/length/weight/hazmat were applied
    traffic: List[TrafficSegment] = Field(default_factory=list)  # live slowdowns (HERE only)
    maneuvers: List[Maneuver] = Field(default_factory=list)  # turn-by-turn (HERE only)
    approach: Optional[Approach] = None  # arrival direction, for entrance directions
    speed_limits: List[SpeedLimit] = Field(default_factory=list)  # posted limits along the route (HERE only)
    incidents: List[Incident] = Field(default_factory=list)  # closures, construction, accidents (HERE only)
    labels: List[str] = Field(default_factory=list)  # main roads, e.g. ["I-80", "CA-89"] (HERE routeLabels)
    tolls_usd: Optional[float] = None  # cheapest fare per toll, summed; None when HERE lists no USD tolls
    alternatives: List["RouteInfo"] = Field(default_factory=list)  # other truck routes, same destination
    handle: Optional[str] = None  # HERE routeHandle: lets the app re-route back onto this route after a deviation
    via: List[List[float]] = Field(default_factory=list)  # stops on the way, [[lat, lng], ...], in order
    section_starts: List[int] = Field(default_factory=list)  # geometry index where each HERE section begins
    weather: List[WeatherAlert] = Field(default_factory=list)  # active NWS alerts along the route
    chain_controls: List[ChainControl] = Field(default_factory=list)  # active Caltrans chain controls on the route


RouteInfo.model_rebuild()

ParkingVerdict = Literal["listed", "likely", "unlikely", "unclear"]


class ParkingSpot(BaseModel):
    kind: Literal["entry", "park"]
    x: float  # 0..1 from the left of the satellite image
    y: float  # 0..1 from the top of the satellite image
    lat: float
    lng: float


class NearbyLot(BaseModel):
    """A truck-capable lot elsewhere in the satellite frame (when the place itself won't fit)."""
    direction: Literal["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"]
    kind: Literal["public lot", "private yard", "unclear"] = "unclear"

    @property
    def phrase(self) -> str:
        look = {"public lot": ", looks like a public lot", "private yard": ", looks like a private yard"}.get(self.kind, "")
        return f"there's a lot with trailers to the {self.direction}{look}"


class ParkingCheck(BaseModel):
    verdict: ParkingVerdict
    label: str  # e.g. "Likely fits a truck (satellite check)"
    reason: str
    source: Literal["place_data", "satellite", "none"]
    image_path: Optional[str] = None  # relative to /api, e.g. "/satellite?lat=..&lng=.."
    entry_tip: Optional[str] = None  # how to drive in
    park_tip: Optional[str] = None  # where to leave the truck
    spots: List[ParkingSpot] = Field(default_factory=list)  # always at the place itself
    relative: bool = False  # entry_tip is relative to the driver's approach ("On your right ...")
    nearby: Optional[NearbyLot] = None  # only with verdict "unlikely"


TRACTOR_FT = 20  # typical tractor length added to the trailer


class TruckProfile(BaseModel):
    height_in: int = Field(162, ge=60, le=203)  # 13 ft 6 in; max 16 ft 11 in
    trailer_ft: int = Field(53, ge=10, le=65)
    weight_lb: int = Field(80000, ge=5000, le=200000)
    hazmat: bool = False
    # Driver preferences from onboarding
    driver_name: Optional[str] = Field(None, max_length=40)
    avoid_tolls: bool = False
    avoid_ferries_tunnels: bool = False
    favorite_chains: List[str] = Field(default_factory=list, max_length=8)
    break_hours: Optional[int] = Field(None, ge=1, le=14)

    @property
    def total_length_ft(self) -> int:
        return self.trailer_ft + TRACTOR_FT

    @property
    def label(self) -> str:
        h = f"{self.height_in // 12} ft {self.height_in % 12} in"
        return (f"{h} tall, {self.trailer_ft} ft trailer (~{self.total_length_ft} ft total), "
                f"{self.weight_lb:,} lb gross{', hazmat' if self.hazmat else ''}")

    @property
    def prefs_label(self) -> str:
        p = []
        if self.driver_name:
            p.append(f"Driver's name: {self.driver_name} (use it now and then, not every reply)")
        if self.avoid_tolls:
            p.append("avoids toll roads")
        if self.avoid_ferries_tunnels:
            p.append("avoids ferries and tunnels")
        if self.favorite_chains:
            p.append("favorite truck stop chains: " + ", ".join(self.favorite_chains))
        if self.break_hours:
            p.append(f"wants a break reminder every {self.break_hours} h")
        return "; ".join(p) or "none"


MAX_VIA = 3  # stops the driver can add on the way (each is one more HERE section)


class RouteRequest(BaseModel):
    lat: float
    lng: float
    place_id: Optional[str] = None
    place: Optional[Place] = None  # preferred: the place object from a previous search
    skip_parking: bool = False  # breakdown providers: the driver needs the shop, not a parking check
    truck_profile: TruckProfile = Field(default_factory=lambda: TruckProfile())
    via: List[List[float]] = Field(default_factory=list, max_length=MAX_VIA)  # stops on the way, [[lat, lng], ...]


class RerouteRequest(BaseModel):
    """The driver left the active route: a new route from where they are, back onto the old one if possible."""
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    place: Place  # the active destination (unchanged)
    handle: Optional[str] = Field(None, max_length=8192)  # RouteInfo.handle of the route being followed
    heading: Optional[int] = Field(None, ge=0, le=359)  # course over ground, so HERE doesn't send a U-turn
    section_index: int = Field(0, ge=0)  # HERE section the driver was on (0 without via stops)
    traveled_m: float = Field(0.0, ge=0)  # metres driven along that section
    truck_profile: TruckProfile = Field(default_factory=TruckProfile)
    via: List[List[float]] = Field(default_factory=list, max_length=MAX_VIA)  # kept when routing fresh


class ParkingRequest(BaseModel):
    place: Place
    approach: Optional[Approach] = None  # from RouteInfo.approach: entrance relative to the driver


class ParkingAnswerRequest(BaseModel):
    place: Place
    wait_s: float = Field(15, ge=1, le=45)


class RouteResponse(BaseModel):
    place: Place
    route: RouteInfo
    parking: Optional[ParkingCheck] = None


class UiState(BaseModel):
    """What the app shows right now (so the copilot knows what it can toggle)."""
    driving: bool = False
    voice_guidance: bool = True
    hands_free: bool = True
    options_count: int = Field(0, ge=0)


class AppAction(BaseModel):
    type: str  # start_navigation | end_route | set_height_in | voice_guidance | reroute | ...
    value: Optional[Union[bool, int, float, str]] = None


class AgentRequest(BaseModel):
    text: str
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    truck_profile: TruckProfile = Field(default_factory=TruckProfile)
    active_place: Optional[Place] = None  # current route destination, for follow-up questions
    session_id: Optional[str] = Field(None, max_length=64)  # one live dialog = one session (memory)
    # Last known distance/time of the active route (the app already has it; no re-routing per turn).
    active_distance_mi: Optional[float] = Field(None, ge=0)
    active_duration_min: Optional[int] = Field(None, ge=0)
    active_speed_limit_mph: Optional[int] = Field(None, ge=0, le=100)  # posted limit where the driver is now
    ui: UiState = Field(default_factory=UiState)


AgentStatus = Literal["options", "route_set", "chat", "no_results", "not_understood", "researching", "providers", "error"]


class ProviderFacts(BaseModel):
    mobile_service: bool | Literal["unknown"] = "unknown"
    hours_24_7: bool | Literal["unknown"] = "unknown"
    truck_focused: bool = False
    prices: List[dict] = Field(default_factory=list)  # {what, amount_usd_low, amount_usd_high, source, quote}
    wait_time: Optional[dict] = None  # {typical_min_low, typical_min_high, quote}
    payment: List[str] = Field(default_factory=list)
    red_flags: List[str] = Field(default_factory=list)
    praise: List[str] = Field(default_factory=list)
    last_review_date: Optional[str] = None


class Provider(BaseModel):
    id: str
    name: str
    lat: float
    lng: float
    phone: Optional[str] = None
    website: Optional[str] = None
    address: Optional[str] = None
    rating: Optional[float] = None
    rating_count: int = 0
    distance_mi: Optional[float] = None
    open_now: Optional[bool] = None
    listed_24_7: bool = False
    shop_only: bool = False
    score: float = 0
    source: str = "google"
    facts: ProviderFacts = Field(default_factory=ProviderFacts)
    quotes: List[str] = Field(default_factory=list)  # verbatim review snippets


class CallCard(BaseModel):
    problem: str
    tire: Optional[str] = None
    location: Optional[str] = None
    gps: str
    map_link: str
    truck: str
    questions: List[str]
    share_text: str


class AgentResponse(BaseModel):
    status: AgentStatus
    transcript: str
    category: Optional[PlaceCategory] = None
    reply: str
    message: Optional[str] = None  # UI message for non-success statuses
    place: Optional[Place] = None
    route: Optional[RouteInfo] = None
    parking: Optional[ParkingCheck] = None
    options: List[Place] = Field(default_factory=list)  # other candidates, closest first
    end_conversation: bool = False  # the dialog reached its end (goodbye / done)
    actions: List[AppAction] = Field(default_factory=list)  # app controls to run (see services/controls.py)
    audio_url: Optional[str] = None  # relative path to the spoken reply (mp3)
    audio_url_2: Optional[str] = None  # rest of a 2-sentence reply, generated in parallel
    timings: dict[str, int] = Field(default_factory=dict)  # ms per pipeline stage (voice turns)
    # Breakdown scenario (services/breakdown.py)
    research_id: Optional[str] = None
    providers: List[Provider] = Field(default_factory=list)
    call_card: Optional[CallCard] = None
    summary: Optional[str] = None
    breakdown_kind: Optional[str] = None
    breakdown_mode: Optional[str] = None
    provider_source: Optional[str] = None
    research_stats: dict = Field(default_factory=dict)
    dial_phone: Optional[str] = None
    source: str
