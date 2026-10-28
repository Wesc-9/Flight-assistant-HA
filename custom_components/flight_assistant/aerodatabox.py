"""Optional AeroDataBox RapidAPI Basic flight lookup.

Flight status endpoint is a Tier 2 call (2 units). This code never asks for
locations, images, flight plans or alerts and never subscribes to a paid tier.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

from aiohttp import ClientError, ClientTimeout
from homeassistant.helpers.aiohttp_client import async_get_clientsession

API_HOST = "aerodatabox.p.rapidapi.com"
API_ROOT = "https://" + API_HOST
DATA_SOURCE = "AeroDataBox (RapidAPI)"
MAX_LOOKAHEAD_DAYS = 365

class AeroDataBoxError(Exception):
    """Provider unavailable, subscription invalid, or exhausted quota."""

def safe_utc(raw):
    """The provider may return UTC ISO without Z or an offset."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc).isoformat()

def parse_flights(raw, number, day, preferred_origin=None):
    """Accept only exact flight/date; return one match or ambiguity status.

    Source JSON is never persisted; outputs only whitelisted flight fields.
    """
    if not isinstance(raw, list):
        raise ValueError("Unexpected AeroDataBox flight response")
    norm = "".join(number.upper().split())
    matches = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        shown = item.get("number")
        if not isinstance(shown, str) or "".join(shown.upper().split()) != norm:
            codeshared = item.get("codeshareStatus")
            # Do not guess a match from a different flight number.
            continue
        dep, arr = item.get("departure") or {}, item.get("arrival") or {}
        if not isinstance(dep, dict) or not isinstance(arr, dict):
            continue
        a, b = dep.get("airport") or {}, arr.get("airport") or {}
        if not isinstance(a, dict) or not isinstance(b, dict):
            continue
        origin, destination = a.get("iata"), b.get("iata")
        if not isinstance(origin, str) or not isinstance(destination, str):
            continue
        origin, destination = origin.upper(), destination.upper()
        if len(origin) != 3 or len(destination) != 3:
            continue
        if preferred_origin and origin != preferred_origin.upper():
            continue
        dep_time, arr_time = dep.get("scheduledTime") or {}, arr.get("scheduledTime") or {}
        if not isinstance(dep_time, dict) or not isinstance(arr_time, dict):
            continue
        local = dep_time.get("local")
        if not isinstance(local, str) or local[:10] != day:
            continue
        dep_utc = safe_utc(dep_time.get("utc"))
        arr_utc = safe_utc(arr_time.get("utc"))
        if not dep_utc or not arr_utc or arr_utc <= dep_utc:
            continue
        matches.append({
            "flight_number": norm,
            "flight_date": day,
            "origin": origin,
            "destination": destination,
            "scheduled": dep_utc,
            "arrival_scheduled": arr_utc,
            "gate": dep.get("gate"),
            "check_in": dep.get("checkInDesk"),
            "status": item.get("status") or "Scheduled",
            "source": DATA_SOURCE,
            "airline_verified": False,
            "last_updated_from_provider": datetime.now(timezone.utc).isoformat(),
        })
    uniques = {(m["origin"], m["destination"], m["scheduled"]): m for m in matches}
    if not uniques:
        return "not_found", None
    if len(uniques) != 1:
        return "ambiguous", None
    return "found", next(iter(uniques.values()))

async def fetch_number_date(hass, api_key, number, day, origin=None):
    """One outbound billed request. Do not log URLs, headers or token."""
    if not api_key or not api_key.strip():
        raise AeroDataBoxError("key_missing")
    days = (date.fromisoformat(day) - datetime.now(timezone.utc).date()).days
    if days < -1 or days > MAX_LOOKAHEAD_DAYS:
        raise AeroDataBoxError("outside_provider_horizon")
    session = async_get_clientsession(hass)
    try:
        async with session.get(
            f"{API_ROOT}/flights/number/{number}/{day}",
            params={"dateLocalRole": "Departure", "withAircraftImage": "false",
                    "withLocation": "false", "withFlightPlan": "false"},
            headers={"X-RapidAPI-Key": api_key, "X-RapidAPI-Host": API_HOST},
            timeout=ClientTimeout(total=20),
        ) as response:
            if response.status in (401, 403):
                raise AeroDataBoxError("invalid_key_or_subscription")
            if response.status == 429:
                raise AeroDataBoxError("free_quota_exhausted")
            if response.status == 404:
                return "not_found", None
            response.raise_for_status()
            payload = await response.json()
        return parse_flights(payload, number, day, origin)
    except AeroDataBoxError:
        raise
    except (ClientError, asyncio.TimeoutError, ValueError, TypeError) as exc:
        raise AeroDataBoxError("provider_unavailable") from exc
