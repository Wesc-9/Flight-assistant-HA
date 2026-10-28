"""Avinor's free XML flight feed. Only airports with IATA identifiers."""
from __future__ import annotations
import asyncio
from aiohttp import ClientError, ClientTimeout
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .const import AVINOR_URL
from .parsing import parse_avinor

async def fetch_departures(hass, airport: str, hours_ahead: int = 24) -> list[dict]:
    """Fetch departures over the requested window (max 336h per Avinor docs).

    Consumers must cache the result and not poll more often than every 3min.
    """
    window = min(336, max(1, int(hours_ahead)))
    session = async_get_clientsession(hass)
    try:
        async with session.get(
            AVINOR_URL,
            params={"airport": airport, "TimeFrom": 6, "TimeTo": window,
                    "direction": "D", "codeshare": "Y"},
            timeout=ClientTimeout(total=22),
        ) as response:
            response.raise_for_status()
            data = await response.text()
        return parse_avinor(data)
    except (ClientError, asyncio.TimeoutError, ValueError) as exc:
        raise RuntimeError(f"Avinor unavailable for {airport}: {exc}") from exc
