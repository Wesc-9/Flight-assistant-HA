"""Avinor public flight feed client."""
from __future__ import annotations
import asyncio
from aiohttp import ClientError, ClientTimeout
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .const import AVINOR_URL
from .parsing import parse_avinor

async def fetch_departures(hass, airport: str) -> list[dict]:
    """Fetch and parse departures. Avinor asks consumers to cache >=3 min."""
    session = async_get_clientsession(hass)
    try:
        async with session.get(
            AVINOR_URL,
            params={"airport": airport, "TimeFrom": 6, "TimeTo": 24,
                    "direction": "D", "codeshare": "Y"},
            timeout=ClientTimeout(total=15),
        ) as response:
            response.raise_for_status()
            data = await response.text()
        return parse_avinor(data)
    except (ClientError, asyncio.TimeoutError, ValueError) as exc:
        raise RuntimeError(f"Avinor unavailable for {airport}: {exc}") from exc
