"""POST /api/weather/points: the weather at sampled points of the route at the truck's ETA (services/route_weather.py)."""
from fastapi import APIRouter

from services import route_weather
from services.route_weather import PointsRequest, RoutePoint

router = APIRouter(prefix="/api")  # included with auth.rate_limited


@router.post("/weather/points")
async def weather_points(req: PointsRequest) -> dict[str, list[RoutePoint]]:
    return {"points": await route_weather.points(req)}
