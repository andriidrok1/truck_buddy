from typing import List, Literal, Optional

from pydantic import BaseModel, Field

Category = Literal["truck_stop", "hotel", "rest_area"]
PlaceCategory = Literal["truck_stop", "hotel", "rest_area", "destination"]


class LatLng(BaseModel):
    lat: float
    lng: float


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
    distance_km: Optional[float] = None
    distance_mi: Optional[float] = None


class PlacesResponse(BaseModel):
    category: Category
    radius_km: float
    widened: bool
    places: List[Place]
    source: str


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


ParkingVerdict = Literal["listed", "likely", "unlikely", "unclear"]


class ParkingSpot(BaseModel):
    kind: Literal["entry", "park"]
    x: float  # 0..1 from the left of the satellite image
    y: float  # 0..1 from the top of the satellite image
    lat: float
    lng: float


class ParkingCheck(BaseModel):
    verdict: ParkingVerdict
    label: str  # e.g. "Likely fits a truck (satellite check)"
    reason: str
    source: Literal["place_data", "satellite", "none"]
    image_path: Optional[str] = None  # relative to /api, e.g. "/satellite?lat=..&lng=.."
    entry_tip: Optional[str] = None  # how to drive in
    park_tip: Optional[str] = None  # where to leave the truck
    spots: List[ParkingSpot] = Field(default_factory=list)


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


class RouteRequest(BaseModel):
    lat: float
    lng: float
    place_id: Optional[str] = None
    place: Optional[Place] = None  # preferred: the place object from a previous search
    truck_profile: TruckProfile = Field(default_factory=lambda: TruckProfile())


class ParkingRequest(BaseModel):
    place: Place


class RouteResponse(BaseModel):
    place: Place
    route: RouteInfo
    parking: Optional[ParkingCheck] = None


class AgentRequest(BaseModel):
    text: str
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    truck_profile: TruckProfile = Field(default_factory=TruckProfile)
    active_place: Optional[Place] = None  # current route destination, for follow-up questions


AgentStatus = Literal["options", "route_set", "chat", "no_results", "not_understood"]


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
    audio_url: Optional[str] = None  # relative path to the spoken reply (mp3)
    source: str
