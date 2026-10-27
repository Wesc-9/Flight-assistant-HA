"""Flight Assistant integration setup and Home Assistant actions."""
from __future__ import annotations

from datetime import datetime
import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, PLATFORMS
from .coordinator import FlightCoordinator
from .parsing import normalize_airport, normalize_flight, parse_datetime
from .planning import default_margin

_LOGGER = logging.getLogger(__name__)
ADD_SCHEMA = vol.Schema({
    vol.Required("flight_number"): cv.string,
    vol.Required("origin"): cv.string,
    vol.Required("scheduled"): cv.string,
    vol.Optional("destination"): cv.string,
    vol.Optional("arrival_scheduled"): cv.string,
    vol.Optional("journey_type", default="domestic"): vol.In(["domestic", "international"]),
    vol.Optional("transport_mode", default="both"): vol.In(["both", "drive", "transit"]),
    vol.Optional("checked_bag", default=False): cv.boolean,
    vol.Optional("airport_margin_minutes"): vol.All(vol.Coerce(int), vol.Range(min=45, max=240)),
    vol.Optional("departure_address"): vol.All(cv.string, vol.Length(max=200)),
    vol.Optional("departure_latitude"): vol.All(vol.Coerce(float), vol.Range(min=-90, max=90)),
    vol.Optional("departure_longitude"): vol.All(vol.Coerce(float), vol.Range(min=-180, max=180)),
})
REMOVE_SCHEMA = vol.Schema({vol.Required("flight_id"): cv.string})

def active_coordinator(hass):
    entries = hass.data.get(DOMAIN, {})
    if not entries:
        raise ServiceValidationError("Configure Flight Assistant first.")
    return next(iter(entries.values()))

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register integration-level actions once."""
    async def add_flight(call: ServiceCall):
        try:
            number = normalize_flight(call.data["flight_number"])
            origin = normalize_airport(call.data["origin"])
            dest = normalize_airport(call.data["destination"]) if call.data.get("destination") else None
            date = parse_datetime(call.data["scheduled"])
            if not date:
                raise ValueError("scheduled must include a timezone, e.g. 2026-11-02T10:30:00+01:00")
        except ValueError as exc:
            raise ServiceValidationError(str(exc)) from exc
        arrival = None
        if call.data.get("arrival_scheduled"):
            arrival = parse_datetime(call.data["arrival_scheduled"])
            if not arrival or arrival <= date:
                raise ServiceValidationError(
                    "arrival_scheduled must be after departure and include a timezone."
                )
        lat = call.data.get("departure_latitude")
        lon = call.data.get("departure_longitude")
        if (lat is None) != (lon is None):
            raise ServiceValidationError("Set both departure_latitude and departure_longitude.")
        margin = call.data.get(
            "airport_margin_minutes", default_margin(call.data["journey_type"])
        )
        if margin % 15:
            raise ServiceValidationError("Airport margin must be a multiple of 15 minutes.")
        prefs = {
            "journey_type": call.data["journey_type"],
            "transport_mode": call.data["transport_mode"],
            "checked_bag": call.data["checked_bag"],
            "airport_margin_minutes": margin,
            "departure_address": call.data.get("departure_address"),
            "departure_latitude": lat,
            "departure_longitude": lon,
        }
        uid = await active_coordinator(hass).async_add_flight(
            number, origin, date, dest, preferences=prefs,
            arrival_scheduled=arrival.isoformat() if arrival else None,
        )
        _LOGGER.info("Flight added with id %s", uid)

    async def remove_flight(call: ServiceCall):
        found = await active_coordinator(hass).async_remove_flight(call.data["flight_id"])
        if not found:
            raise ServiceValidationError("Flight id was not found.")

    hass.services.async_register(DOMAIN, "add_flight", add_flight, schema=ADD_SCHEMA)
    hass.services.async_register(DOMAIN, "remove_flight", remove_flight, schema=REMOVE_SCHEMA)
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = FlightCoordinator(hass, entry)
    await coordinator.async_load_flights()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await coordinator.async_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_options_update))
    return True

async def async_options_update(hass: HomeAssistant, entry: ConfigEntry):
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
