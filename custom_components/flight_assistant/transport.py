"""Optional Waze and Entur transport suggestions."""
from __future__ import annotations
import asyncio
from datetime import datetime
import logging
from aiohttp import ClientError, ClientTimeout
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .const import ENTUR_URL

_LOGGER = logging.getLogger(__name__)

async def get_waze_minutes(hass, origin: tuple, destination: tuple) -> float | None:
    """Use HA's own Waze action if the integration is installed."""
    if not hass.services.has_service("waze_travel_time", "get_travel_times"):
        return None
    try:
        result = await hass.services.async_call(
            "waze_travel_time", "get_travel_times",
            {"origin": f"{origin[0]},{origin[1]}",
             "destination": f"{destination[0]},{destination[1]}",
             "region": "eu", "realtime": True, "units": "metric"},
            blocking=True, return_response=True,
        )
        values = [route.get("duration") for route in (result or {}).get("routes", [])
                  if isinstance(route.get("duration"), (int, float)) and route["duration"] > 0]
        return round(min(values), 1) if values else None
    except Exception as exc:  # HA services can raise validation or provider errors
        _LOGGER.warning("Waze travel-time lookup failed: %s", exc)
        return None

TRIP_QUERY = """
query($fromLat: Float!, $fromLon: Float!, $toLat: Float!, $toLon: Float!, $when: DateTime!) {
 trip(
   from: {coordinates: {latitude: $fromLat, longitude: $fromLon}}
   to: {coordinates: {latitude: $toLat, longitude: $toLon}}
   dateTime: $when
   arriveBy: true
   numTripPatterns: 6
 ) {
   tripPatterns {
     duration
     legs {
       mode
       expectedStartTime
       expectedEndTime
       fromPlace {name}
       toPlace {name}
       line {publicCode name}
     }
   }
 }
}
"""

async def get_entur_journey(
    hass, origin: tuple, destination: tuple, arrival_target: datetime
) -> dict | None:
    """Pick latest itinerary arriving before airport arrival target."""
    session = async_get_clientsession(hass)
    variables = {
        "fromLat": origin[0], "fromLon": origin[1],
        "toLat": destination[0], "toLon": destination[1],
        "when": arrival_target.isoformat(),
    }
    try:
        async with session.post(
            ENTUR_URL, json={"query": TRIP_QUERY, "variables": variables},
            headers={"ET-Client-Name": "wesc9-flight-assistant"},
            timeout=ClientTimeout(total=20),
        ) as response:
            response.raise_for_status()
            payload = await response.json()
        if payload.get("errors"):
            _LOGGER.warning("Entur query error: %s", payload["errors"])
            return None
        candidates = []
        for pattern in (payload.get("data") or {}).get("trip", {}).get("tripPatterns", []):
            legs = pattern.get("legs") or []
            if not legs:
                continue
            first = legs[0].get("expectedStartTime")
            last = legs[-1].get("expectedEndTime")
            from .parsing import parse_datetime
            first_dt, last_dt = parse_datetime(first), parse_datetime(last)
            if first_dt and last_dt and last_dt <= arrival_target:
                candidates.append((first_dt, last_dt, pattern))
        if not candidates:
            return None
        first_dt, last_dt, pattern = max(candidates, key=lambda item: item[0])
        segments = []
        for leg in pattern["legs"]:
            line = leg.get("line") or {}
            segments.append({
                "mode": leg.get("mode"),
                "route": line.get("publicCode") or line.get("name"),
                "from": (leg.get("fromPlace") or {}).get("name"),
                "to": (leg.get("toPlace") or {}).get("name"),
                "start": leg.get("expectedStartTime"),
                "end": leg.get("expectedEndTime"),
            })
        return {
            "leave_at": first_dt.isoformat(),
            "arrive_at": last_dt.isoformat(),
            "minutes": round((last_dt - first_dt).total_seconds() / 60),
            "legs": segments,
            "source": "Entur",
        }
    except (ClientError, asyncio.TimeoutError, ValueError, TypeError, KeyError) as exc:
        _LOGGER.warning("Entur trip planning failed: %s", exc)
        return None
