"""Flight Assistant persistent state, live updates and transport coordination."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from uuid import uuid4

from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from .avinor import fetch_departures
from .const import (
    DOMAIN, STORAGE_VERSION, UPDATE_INTERVAL_SECONDS,
    AIRPORTS, DEFAULT_MARGIN_MINUTES, DEFAULT_EXTRA_DRIVE_MINUTES,
)
from .parsing import match_departure, parse_datetime, airport_arrival_target, recommended_drive_departure
from .transport import get_waze_minutes, get_entur_journey

_LOGGER = logging.getLogger(__name__)

class FlightCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(
            hass, _LOGGER, name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.entry = entry
        self.store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self.flights: dict[str, dict] = {}
        self.transport: dict[str, dict] = {}
        self._drive_cache: dict[str, tuple] = {}
        self._entur_cache: dict[str, tuple] = {}
        self._alerts: set[str] = set()

    def setting(self, name, default=None):
        return self.entry.options.get(name, self.entry.data.get(name, default))

    async def async_load_flights(self):
        saved = await self.store.async_load() or {}
        self.flights = saved.get("flights", {})

    async def async_add_flight(self, flight_number, origin, scheduled, destination=None):
        uid = uuid4().hex
        self.flights[uid] = {
            "id": uid, "flight_number": flight_number, "origin": origin,
            "scheduled": scheduled.isoformat(), "destination": destination,
            "status": "Planned", "flight_data": "Not yet verified",
            "gate": None, "check_in": None, "expected": None,
        }
        await self.store.async_save({"flights": self.flights})
        await self.async_request_refresh()
        return uid

    async def async_remove_flight(self, uid):
        if uid not in self.flights:
            return False
        del self.flights[uid]
        self.transport.pop(uid, None)
        self._entur_cache.pop(uid, None)
        await self.store.async_save({"flights": self.flights})
        await self.async_request_refresh()
        return True

    async def _async_update_data(self):
        now = dt_util.utcnow()
        airports = {
            item["origin"] for item in self.flights.values()
            if (stamp := parse_datetime(item.get("scheduled")))
            and -timedelta(hours=6) <= stamp - now <= timedelta(hours=24)
        }
        feeds = {}
        for airport in airports:
            try:
                feeds[airport] = await fetch_departures(self.hass, airport)
            except RuntimeError as exc:
                _LOGGER.warning("%s", exc)

        dirty = False
        for uid, flight in self.flights.items():
            scheduled = parse_datetime(flight.get("scheduled"))
            if not scheduled:
                continue
            if flight["origin"] in feeds:
                found = match_departure(
                    feeds[flight["origin"]], flight["flight_number"], scheduled
                )
                if found:
                    updates = {
                        "flight_data": "Avinor",
                        "status": {
                            "A": "Arrived", "C": "Cancelled",
                            "D": "Departed", "E": "New time",
                            "N": "New information",
                        }.get(found["status_code"], "Scheduled"),
                        "gate": found["gate"],
                        "check_in": found["check_in"],
                        "expected": found["expected"] if parse_datetime(found["expected"]) else None,
                        "destination": found["destination"] or flight.get("destination"),
                    }
                    changes = {
                        key: (flight.get(key), val)
                        for key, val in updates.items() if flight.get(key) != val
                    }
                    if changes:
                        flight.update(updates)
                        dirty = True
                        # No passenger or booking details ever enter HA events.
                        self.hass.bus.async_fire(f"{DOMAIN}_changed", {
                            "flight_id": uid, "flight_number": flight["flight_number"],
                            "changes": list(changes),
                        })
            if timedelta(hours=-1) <= scheduled - now <= timedelta(hours=36):
                await self._refresh_transport(uid, flight, scheduled, now)
            else:
                self.transport.pop(uid, None)

        if dirty:
            await self.store.async_save({"flights": self.flights})
        return dict(self.flights)

    async def _refresh_transport(self, uid, flight, scheduled, now):
        airport = flight["origin"]
        coords = AIRPORTS.get(airport)
        if not coords:
            return
        origin = (self.setting("home_latitude"), self.setting("home_longitude"))
        if any(coord is None for coord in origin):
            return
        margin = self.setting("airport_margin_minutes", DEFAULT_MARGIN_MINUTES)
        buffer = self.setting("drive_buffer_minutes", DEFAULT_EXTRA_DRIVE_MINUTES)
        arrival = airport_arrival_target(scheduled, margin)
        data = self.transport.setdefault(uid, {})
        data["arrive_airport_by"] = arrival.isoformat()
        data["airport"] = airport
        # Waze returns current road conditions, not a guaranteed future forecast.
        if scheduled - now <= timedelta(hours=12):
            cached = self._drive_cache.get(airport)
            if not cached or now - cached[0] >= timedelta(minutes=10):
                minutes = await get_waze_minutes(self.hass, origin, coords)
                self._drive_cache[airport] = (now, minutes)
            else:
                minutes = cached[1]
            data["drive_minutes"] = minutes
            data["drive_leave_at"] = (
                recommended_drive_departure(scheduled, margin, minutes, buffer).isoformat()
                if minutes is not None else None
            )
        else:
            data["drive_minutes"] = None
            data["drive_leave_at"] = None
        if timedelta(hours=0) <= arrival - now <= timedelta(days=3):
            cached = self._entur_cache.get(uid)
            if not cached or now - cached[0] >= timedelta(minutes=15):
                route = await get_entur_journey(self.hass, origin, coords, arrival)
                self._entur_cache[uid] = (now, route)
            else:
                route = cached[1]
            data["public_transport"] = route
        else:
            data["public_transport"] = None
        # Emit a single event per flight/mode and phase, not a push notification.
        for mode, departure in (
            ("drive", data.get("drive_leave_at")),
            ("public_transport", (data.get("public_transport") or {}).get("leave_at")),
        ):
            when = parse_datetime(departure)
            if not when:
                continue
            minutes_left = (when - now).total_seconds() / 60
            phase = "soon" if 0 < minutes_left <= 20 else ("now" if -3 <= minutes_left <= 0 else None)
            token = f"{uid}:{mode}:{phase}"
            if phase and token not in self._alerts:
                self._alerts.add(token)
                self.hass.bus.async_fire(f"{DOMAIN}_leave_reminder", {
                    "flight_id": uid, "flight_number": flight["flight_number"],
                    "mode": mode, "phase": phase, "leave_at": departure,
                })
