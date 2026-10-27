"""Next flight and per-flight sensors."""
from __future__ import annotations

from datetime import timedelta
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util
from homeassistant.helpers.device_registry import DeviceInfo
from .const import DOMAIN
from .planning import time_progress_percent
from .parsing import parse_datetime

async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data["flight_assistant"][entry.entry_id]
    active: dict[str, FlightSensor] = {}

    def add_new():
        additions = []
        for uid in coordinator.flights:
            if uid not in active:
                flight_sensor = FlightSensor(coordinator, uid)
                countdown = FlightCountdownSensor(coordinator, uid)
                active[uid] = (flight_sensor, countdown)
                additions.extend([flight_sensor, countdown])
        for uid in list(active):
            if uid not in coordinator.flights:
                for sensor in active.pop(uid):
                    hass.async_create_task(sensor.async_remove())
        if additions:
            async_add_entities(additions)

    async_add_entities([NextFlightSensor(coordinator)])
    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))

class FlightSensor(CoordinatorEntity, SensorEntity):
    _attr_icon = "mdi:airplane"

    def __init__(self, coordinator, uid):
        super().__init__(coordinator)
        self.uid = uid
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{uid}"
        record = coordinator.flights.get(uid, {})
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, uid)},
            name=f"{record.get('flight_number', 'Flight')} {record.get('origin', '')} travel",
            manufacturer="Flight Assistant",
            model="Flight itinerary",
        )

    @property
    def name(self):
        item = self.coordinator.flights.get(self.uid, {})
        return f"Flight Assistant {item.get('flight_number', 'Flight')} {item.get('origin', '')}"

    @property
    def native_value(self):
        item = self.coordinator.flights.get(self.uid)
        return item.get("status") if item else None

    @property
    def extra_state_attributes(self):
        item = self.coordinator.flights.get(self.uid, {})
        return {**item, **self.coordinator.transport.get(self.uid, {}),
                "attribution": "Flydata fra Avinor – https://www.avinor.no"}

class FlightCountdownSensor(FlightSensor):
    """Countdown in minutes, linked to the same HA flight device."""
    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = "min"

    def __init__(self, coordinator, uid):
        super().__init__(coordinator, uid)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{uid}_countdown"

    @property
    def name(self):
        return f"{super().name} minutes to departure"

    @property
    def native_value(self):
        record = self.coordinator.flights.get(self.uid, {})
        depart = parse_datetime(record.get("scheduled"))
        return round(max(0, (depart - dt_util.utcnow()).total_seconds()) / 60) if depart else None

    @property
    def extra_state_attributes(self):
        return {}

class NextFlightSensor(CoordinatorEntity, SensorEntity):
    _attr_name = "Flight Assistant Next Flight"
    _attr_icon = "mdi:airplane-clock"

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_next_flight"

    @property
    def _next(self):
        now = dt_util.utcnow()
        future = [
            (parse_datetime(flight.get("scheduled")), flight)
            for flight in self.coordinator.flights.values()
        ]
        future = [
            (when, flight) for when, flight in future
            if when and when >= now - timedelta(hours=6)
        ]
        return min(future, key=lambda x: x[0])[1] if future else None

    @property
    def native_value(self):
        flight = self._next
        return flight["flight_number"] if flight else "No upcoming flights"

    @property
    def extra_state_attributes(self):
        flight = self._next
        if not flight:
            return {"count": len(self.coordinator.flights)}
        arrival = parse_datetime(flight.get("arrival_scheduled"))
        departure = parse_datetime(flight.get("scheduled"))
        airborne = flight.get("status") == "Departed"
        return {
            **flight, **self.coordinator.transport.get(flight["id"], {}),
            "count": len(self.coordinator.flights),
            "minutes_to_departure": (round((departure - dt_util.utcnow()).total_seconds() / 60)
                                     if departure else None),
            "airborne": airborne,
            "estimated_journey_progress_percent": (
                time_progress_percent(departure, arrival, dt_util.utcnow())
                if airborne and departure and arrival else None
            ),
            "aircraft_live_position_available": False,
            "attribution": "Flydata fra Avinor – https://www.avinor.no",
        }
