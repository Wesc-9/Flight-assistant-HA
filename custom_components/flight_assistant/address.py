"""Address search via Entur Geocoder v3: no automatic background requests."""
from __future__ import annotations
import asyncio
from aiohttp import ClientError, ClientTimeout
from homeassistant.helpers.aiohttp_client import async_get_clientsession

URL = "https://api.entur.io/geocoder/v3/autocomplete"

class AddressError(Exception):
    """A Norwegian street address was not resolved."""

def parse_candidates(payload):
    """Geocoder v3 GeoJSON coordinates are [longitude, latitude]."""
    results = []
    if not isinstance(payload, dict):
        return results
    for feature in payload.get("features", [])[:6]:
        if not isinstance(feature, dict):
            continue
        coords = (feature.get("geometry") or {}).get("coordinates")
        names = (feature.get("properties") or {}).get("names") or {}
        if not isinstance(coords, list) or len(coords) < 2:
            continue
        if not isinstance(names, dict):
            continue
        lon, lat = coords[:2]
        label = names.get("display") or names.get("default")
        if isinstance(lon, bool) or isinstance(lat, bool):
            continue
        if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
            continue
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            continue
        if not isinstance(label, str) or not label.strip():
            continue
        results.append({"label": label.strip(), "latitude": float(lat), "longitude": float(lon)})
    return results

async def lookup(hass, address):
    address = address.strip()
    if not 5 <= len(address) <= 200:
        raise AddressError("invalid_address")
    try:
        session = async_get_clientsession(hass)
        async with session.get(
            URL,
            params={"q": address, "lang": "no", "limit": 6, "layers": "address"},
            headers={"ET-Client-Name": "wesc9-flight-assistant"},
            timeout=ClientTimeout(total=12),
        ) as response:
            response.raise_for_status()
            candidates = parse_candidates(await response.json())
        if not candidates:
            raise AddressError("address_not_found")
        return candidates
    except (ClientError, asyncio.TimeoutError, ValueError, TypeError) as exc:
        raise AddressError("address_unavailable") from exc
