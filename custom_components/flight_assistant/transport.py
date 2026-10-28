"""Free public transport (Entur) and optional Waze travel-time suggestions.

Entur requires no API token. Waze requires the optional, built-in
Home Assistant Waze Travel Time integration and has no API key field here.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from aiohttp import ClientError, ClientTimeout
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import ENTUR_URL
from .parsing import parse_datetime
from .transport_parsing import shortest_waze_route, parse_entur_patterns

_LOGGER = logging.getLogger(__name__)


async def get_waze_minutes(hass, origin: tuple | str, destination: tuple,
                           vehicle_type: str = "car") -> float | None:
    """Find shortest live drive route via HA's service. Duration is minutes."""
    if not hass.services.has_service("waze_travel_time", "get_travel_times"):
        return None
    try:
        result = await hass.services.async_call(
            "waze_travel_time", "get_travel_times",
            {
                "origin": origin if isinstance(origin, str)
                else f"{origin[0]},{origin[1]}",
                "destination": f"{destination[0]},{destination[1]}",
                "region": "eu",
                "realtime": True,
                "units": "metric",
                "vehicle_type": "taxi" if vehicle_type == "taxi" else "car",
            },
            blocking=True,
            return_response=True,
        )
        return shortest_waze_route(result)
    except Exception as exc:
        # An unavailable optional integration must not break air-travel updates.
        _LOGGER.warning("Waze travel-time lookup unavailable: %s", type(exc).__name__)
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
    hass, origin: tuple, destination: tuple, arrival_target: datetime,
    preferred_mode: str | None = None,
) -> dict | None:
    """Return last feasible public-transport journey arriving before target."""
    variables = {
        "fromLat": float(origin[0]), "fromLon": float(origin[1]),
        "toLat": float(destination[0]), "toLon": float(destination[1]),
        "when": arrival_target.isoformat(),
    }
    session = async_get_clientsession(hass)
    try:
        async with session.post(
            ENTUR_URL,
            json={"query": TRIP_QUERY, "variables": variables},
            headers={"ET-Client-Name": "wesc9-flight-assistant",
                     "Content-Type": "application/json"},
            timeout=ClientTimeout(total=20),
        ) as response:
            response.raise_for_status()
            payload = await response.json()
        if payload.get("errors"):
            _LOGGER.warning("Entur journey planner rejected query")
            return None
        return parse_entur_patterns(payload, arrival_target, parse_datetime, preferred_mode)
    except (ClientError, asyncio.TimeoutError, TypeError, ValueError, KeyError) as exc:
        _LOGGER.warning("Entur journey planner unavailable: %s", type(exc).__name__)
        return None
