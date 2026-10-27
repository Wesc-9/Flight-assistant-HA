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
from .planning import arrival_windows, driving_leave_at
import asyncio

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

    async def async_add_flight(self, flight_number, origin, scheduled, destination=None,
                               preferences=None, arrival_scheduled=None):
        uid = uuid4().hex
        self.flights[uid] = {
            "id": uid, "flight_number": flight_number, "origin": origin,
            "scheduled": scheduled.isoformat(), "destination": destination,
            "status": "Planned", "flight_data": "Not yet verified",
            "gate": None, "check_in": None, "expected": None,
            "preferences": preferences or {},
            "arrival_scheduled": arrival_scheduled,
        }
        await self.store.async_save({"flights": self.flights})
        await self.async_request_refresh()
        return uid

    async def async_remove_flight(self, uid):
        if uid not in self.flights:
            return False
        del self.flights[uid]
        self.transport.pop(uid, None)
        for key in list(self._entur_cache):
            if key == uid or (isinstance(key, tuple) and key[0] == uid):
                self._entur_cache.pop(key, None)
        self._drive_cache.pop(uid, None)
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
            if flight.get("status") != "Cancelled" and timedelta(hours=0) <= scheduled - now <= timedelta(hours=36):
                await self._refresh_transport(uid, flight, scheduled, now)
            else:
                self.transport.pop(uid, None)

        if dirty:
            await self.store.async_save({"flights": self.flights})
        return dict(self.flights)

    async def _refresh_transport(self, uid, flight, scheduled, now):
        """Calculate three airport arrival windows, each with available transport."""
        airport = flight["origin"]
        coords = AIRPORTS.get(airport)
        if not coords:
            self.transport[uid] = {"reason": "airport_location_not_supported"}
            return

        prefs = flight.get("preferences") or {}
        journey_type = prefs.get("journey_type", "international")
        if journey_type not in ("domestic", "international"):
            journey_type = "international"
        margin = int(prefs.get(
            "airport_margin_minutes",
            self.setting("airport_margin_minutes", DEFAULT_MARGIN_MINUTES)
        ))
        # Older saved flights may use non-step-specific margins.
        margin = max(45, min(240, round(margin / 15) * 15))
        checked_bag = bool(prefs.get("checked_bag", False))
        mode = prefs.get("transport_mode", "both")
        drive_enabled = mode in ("both", "drive")
        transit_enabled = mode in ("both", "transit")
        address = prefs.get("departure_address")
        manual_lat = prefs.get("departure_latitude")
        manual_lon = prefs.get("departure_longitude")
        has_manual_coords = manual_lat is not None and manual_lon is not None
        origin_coords = ((manual_lat, manual_lon) if has_manual_coords else (
            self.setting("home_latitude"), self.setting("home_longitude")))
        if any(x is None for x in origin_coords):
            self.transport[uid] = {"reason": "origin_coordinates_missing"}
            return
        # Address-only origins can be looked up in Waze, but not Entur.
        # Never silently route transit from HA's home coordinates for an address override.
        transit_coords = None if address and not has_manual_coords else origin_coords
        driving_origin = address if address else origin_coords
        buffer = self.setting("drive_buffer_minutes", DEFAULT_EXTRA_DRIVE_MINUTES)
        windows = arrival_windows(scheduled, margin, journey_type, checked_bag)
        data = self.transport.setdefault(uid, {})
        data.update({
            "airport": airport, "origin_address": address,
            "transport_mode": mode,
            "arrive_airport_by": windows[1]["airport_arrival_target"],
            "airport_arrival_margin_minutes": margin,
            "airline_check_in_cutoff_verified": False,
            "reason": None,
        })

        drive_minutes = None
        if drive_enabled and scheduled - now <= timedelta(hours=12):
            cached = self._drive_cache.get(uid)
            if cached and now - cached[0] < timedelta(minutes=10):
                drive_minutes = cached[1]
            else:
                drive_minutes = await get_waze_minutes(self.hass, driving_origin, coords)
                self._drive_cache[uid] = (now, drive_minutes)
        data["drive_minutes"] = drive_minutes
        data["driving_source"] = "Waze (current conditions)" if drive_minutes is not None else None

        # Match each arrival window against a REAL Entur itinerary.
        async def transit_for(window):
            target = parse_datetime(window["airport_arrival_target"])
            if not transit_enabled or transit_coords is None or not target:
                return None
            if not timedelta(0) <= target - now <= timedelta(days=3):
                return None
            cache_key = (uid, window["airport_arrival_target"])
            cached = self._entur_cache.get(cache_key)
            if cached and now - cached[0] < timedelta(minutes=15):
                return cached[1]
            result = await get_entur_journey(self.hass, transit_coords, coords, target)
            self._entur_cache[cache_key] = (now, result)
            return result

        routes = await asyncio.gather(*(transit_for(w) for w in windows))
        suggestions = []
        for window, transit in zip(windows, routes):
            target = parse_datetime(window["airport_arrival_target"])
            drive = ({
                "leave_at": driving_leave_at(target, drive_minutes, buffer),
                "travel_minutes": drive_minutes,
                "parking_buffer_minutes": buffer,
                "source": "Waze (live traffic, not a future forecast)",
            } if drive_enabled and drive_minutes is not None else None)
            suggestions.append({
                **window, "drive": drive,
                "public_transport": transit,
                "transport_available": drive is not None or transit is not None,
            })
        data["suggestions"] = suggestions
        # Legacy attributes and existing dashboard templates use recommendation #2.
        standard = next((v for v in suggestions if v["number"] == 2), None)
        data["drive_leave_at"] = (standard.get("drive") or {}).get("leave_at") if standard else None
        data["public_transport"] = standard.get("public_transport") if standard else None
        if address and not has_manual_coords and transit_enabled:
            data["reason"] = "Transit needs address coordinates; Waze can use the address text."
        elif drive_enabled and drive_minutes is None and transit_enabled and all(not x for x in routes):
            data["reason"] = "No confirmed transport itineraries"

        # Fire departure reminder events for suggestion #2 only.
        for transport_mode, departure in (
            ("drive", data.get("drive_leave_at")),
            ("public_transport", (data.get("public_transport") or {}).get("leave_at")),
        ):
            when = parse_datetime(departure)
            if when is None:
                continue
            minutes_left = (when - now).total_seconds() / 60
            phase = "soon" if 0 < minutes_left <= 20 else (
                "now" if -3 <= minutes_left <= 0 else None)
            token = f"{uid}:{transport_mode}:{phase}"
            if phase and token not in self._alerts:
                self._alerts.add(token)
                self.hass.bus.async_fire(f"{DOMAIN}_leave_reminder", {
                    "flight_id": uid, "flight_number": flight["flight_number"],
                    "mode": transport_mode, "phase": phase, "leave_at": departure,
                })
