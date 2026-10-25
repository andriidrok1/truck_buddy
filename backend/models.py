from typing import List, Literal, Optional

from pydantic import BaseModel, Field

Category = Literal["truck_stop", "hotel", "rest_area"]


class LatLng(BaseModel):
    lat: float
    lng: float


class Place(BaseModel):
    id: str
    name: str
    category: Category
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
    summary: str  # "12 min · 6.2 mi"
    source: str


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


class RouteRequest(BaseModel):
    lat: float
    lng: float
    place_id: Optional[str] = None
    place: Optional[Place] = None  # preferred: the place object from a previous search


class ParkingRequest(BaseModel):
    place: Place


class RouteResponse(BaseModel):
    place: Place
    route: RouteInfo
    parking: Optional[ParkingCheck] = None


class TruckProfile(BaseModel):
    height: str = "13 ft 6 in"
    trailer_length: str = "53 ft trailer"


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
    category: Optional[Category] = None
    reply: str
    message: Optional[str] = None  # UI message for non-success statuses
    place: Optional[Place] = None
    route: Optional[RouteInfo] = None
    parking: Optional[ParkingCheck] = None
    options: List[Place] = Field(default_factory=list)  # other candidates, closest first
    audio_url: Optional[str] = None  # relative path to the spoken reply (mp3)
    source: str
