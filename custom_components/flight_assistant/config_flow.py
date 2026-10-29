"""Flight Assistant config and additional bookings as Home Assistant subentries.

Important: booking references are not queried until an approved airline integration
is available. They must never be put in entity attributes, service responses or logs.
"""
from __future__ import annotations
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from .address import AddressError, lookup
from .const import DOMAIN, DEFAULT_EXTRA_DRIVE_MINUTES
from .display import minutes_text
from .parsing import normalize_flight, normalize_airport
from datetime import date
from homeassistant.config_entries import FlowType, SOURCE_USER, SubentryFlowContext
from .trip_setup import airline_from_number, make_segment
from .provider_settings import (
    SOURCE_AUTO, SOURCE_AERODATABOX, VALID_SOURCES, source_needs_key,
)

def text_field(password=False):
    return selector.TextSelector(selector.TextSelectorConfig(
        type=selector.TextSelectorType.PASSWORD if password else selector.TextSelectorType.TEXT
    ))

def slider(low, high, step):
    return selector.NumberSelector(selector.NumberSelectorConfig(
        min=low, max=high, step=step,
        mode=selector.NumberSelectorMode.SLIDER, unit_of_measurement="min",
    ))

def select(items):
    return selector.SelectSelector(selector.SelectSelectorConfig(
        options=[selector.SelectOptionDict(value=k, label=v) for k, v in items],
        mode=selector.SelectSelectorMode.DROPDOWN,
    ))

MODES = [
    ("walk", "Gå (ruteberegning kommer senere)"),
    ("bus", "Buss"),
    ("taxi", "Taxi"),
    ("train", "Tog"),
    ("drive", "Kjøre selv"),
    ("both", "Sammenlign bil og kollektiv"),
    ("transit", "Kollektiv – alle"),
    ("bike", "Sykkel (ruteberegning kommer senere)"),
]
TYPES = [("domestic", "Innenlands"), ("international", "Utenlands")]
AIRLINES = {"sas": "SAS", "norwegian": "Norwegian", "wideroe": "Widerøe"}
FLIGHT_DATA_SOURCES = [
    ("auto", "Automatisk (AeroDataBox med nøkkel, ellers Avinor)"),
    ("avinor", "Kun Avinor (gratis, opptil 14 dager frem)"),
    ("aerodatabox", "AeroDataBox + Avinor (RapidAPI-nøkkel kreves)"),
]


def global_schema(values, include_provider=False):
    fields = {
        vol.Optional("home_address", default=values.get("home_address", "")): text_field(),
        vol.Required("airport_margin_minutes",
                     default=values.get("airport_margin_minutes", 90)): slider(45, 240, 15),
        vol.Required("drive_buffer_minutes",
                     default=values.get("drive_buffer_minutes", DEFAULT_EXTRA_DRIVE_MINUTES)):
            slider(0, 120, 5),
    }
    if include_provider:
        fields[vol.Required(
            "flight_data_source",
            default=values.get("flight_data_source", SOURCE_AUTO),
        )] = select(FLIGHT_DATA_SOURCES)
        # Blank means keep stored key; do not echo a saved secret into the form.
        fields[vol.Optional("aerodatabox_api_key")] = text_field(password=True)
        fields[vol.Optional("clear_aerodatabox_key", default=False)] = selector.BooleanSelector()
    return vol.Schema(fields)

def api_schema(values=None):
    """Provider selection and credentials live only in the parent config."""
    values = values or {}
    return vol.Schema({
        vol.Required("flight_data_source", default=values.get(
            "flight_data_source", SOURCE_AUTO,
        )): select(FLIGHT_DATA_SOURCES),
        vol.Optional("aerodatabox_api_key"): text_field(password=True),
    })


def booking_schema(values):
    """One cohesive form: the final field is the airport arrival slider."""
    return vol.Schema({
        vol.Required("trip_name", default=values.get("trip_name", "")): text_field(),
        vol.Required("flight_number", default=values.get("flight_number", "")):
            text_field(),
        vol.Required("flight_date", default=values.get(
            "flight_date", date.today().isoformat())): selector.DateSelector(),
        vol.Optional("departure_airport", default=values.get("departure_airport", "")):
            text_field(),
        vol.Required("has_stopover", default=values.get("has_stopover", False)):
            selector.BooleanSelector(),
        vol.Required("checked_bag", default=values.get("checked_bag", False)):
            selector.BooleanSelector(),
        vol.Required("transport_mode", default=values.get("transport_mode", "both")):
            select(MODES),
        vol.Optional("origin_address", default=values.get("origin_address", "")):
            text_field(),
        vol.Required("journey_type", default=values.get("journey_type", "domestic")):
            select(TYPES),
        # Last field on the registration screen, as requested.
        vol.Required("airport_margin_minutes",
                     default=values.get("airport_margin_minutes", 90)):
            slider(45, 240, 15),
    })


def flight_schema(values):
    return vol.Schema({
        vol.Required("flight_number", default=values.get("flight_number", "")):
            text_field(),
        vol.Required("flight_date", default=values.get(
            "flight_date", date.today().isoformat())): selector.DateSelector(),
        vol.Optional("departure_airport", default=values.get("departure_airport", "")):
            text_field(),
    })


def more_flights_schema():
    return vol.Schema({
        vol.Required("add_another_flight", default=False): selector.BooleanSelector(),
    })


def match_schema(matches):
    return vol.Schema({
        vol.Required("address_match", default="0"):
            select([(str(i), item["label"]) for i, item in enumerate(matches)])
    })

class AddressSteps:
    """Shared address verification methods for main and options flows."""
    def _setup_address(self):
        self._pending = None
        self._matches = []

    async def _resolve_address(self, data, field):
        entered = (data.get(field) or "").strip()
        data[field] = entered
        if not entered:
            data["home_latitude"] = self.hass.config.latitude
            data["home_longitude"] = self.hass.config.longitude
            data["resolved_home_address"] = ""
            return data, False
        self._pending = data
        self._matches = await lookup(self.hass, entered)
        return data, True

    def _selected_address(self, choice, field="home_address"):
        selected = self._matches[int(choice["address_match"])]
        data = dict(self._pending)
        data["resolved_" + field] = selected["label"]
        if field == "home_address":
            data["home_latitude"] = selected["latitude"]
            data["home_longitude"] = selected["longitude"]
        else:
            data["departure_latitude"] = selected["latitude"]
            data["departure_longitude"] = selected["longitude"]
        return data

    def _show_address_choice(self, step):
        return self.async_show_form(
            step_id=step, data_schema=match_schema(self._matches),
            description_placeholders={
                "airport_margin": minutes_text(self._pending["airport_margin_minutes"]),
                "drive_buffer": minutes_text(self._pending.get("drive_buffer_minutes", 0)),
            },
        )

class FlightAssistantConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Step 1: API key. Next: open the Add Trip flow automatically."""
    VERSION = 1

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry):
        return {"booking": BookingSubentryFlow}

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            source = user_input.get("flight_data_source", SOURCE_AUTO)
            key = (user_input.get("aerodatabox_api_key") or "").strip()
            if source not in VALID_SOURCES:
                return self.async_show_form(
                    step_id="user", data_schema=api_schema(user_input),
                    errors={"flight_data_source": "invalid_source"},
                )
            if source_needs_key(source) and not key:
                return self.async_show_form(
                    step_id="user", data_schema=api_schema(user_input),
                    errors={"aerodatabox_api_key": "key_required"},
                )
            return self.async_create_entry(
                title="Flight Assistant",
                data={
                    "flight_data_source": source,
                    "aerodatabox_api_key": key,
                    "home_latitude": self.hass.config.latitude,
                    "home_longitude": self.hass.config.longitude,
                    "airport_margin_minutes": 90,
                    "drive_buffer_minutes": DEFAULT_EXTRA_DRIVE_MINUTES,
                },
            )
        return self.async_show_form(step_id="user", data_schema=api_schema())

    async def async_on_create_entry(self, result):
        """Continue directly to first trip after integration is created."""
        next_result = await self.hass.config_entries.subentries.async_init(
            (result["result"].entry_id, "booking"),
            context=SubentryFlowContext(source=SOURCE_USER),
        )
        result["next_flow"] = (
            FlowType.CONFIG_SUBENTRIES_FLOW, next_result["flow_id"]
        )
        return result

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return FlightAssistantOptionsFlow()


class FlightAssistantOptionsFlow(AddressSteps, config_entries.OptionsFlow):
    def __init__(self):
        self._setup_address()

    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                values = dict(user_input)
                existing_key = self.config_entry.options.get(
                    "aerodatabox_api_key",
                    self.config_entry.data.get("aerodatabox_api_key", ""),
                )
                key = (values.pop("aerodatabox_api_key", "") or "").strip()
                clear = values.pop("clear_aerodatabox_key", False)
                values["aerodatabox_api_key"] = "" if clear else (key or existing_key)
                source = values.get("flight_data_source", SOURCE_AUTO)
                if source not in VALID_SOURCES:
                    return self.async_show_form(
                        step_id="init",
                        data_schema=global_schema(values, include_provider=True),
                        errors={"flight_data_source": "invalid_source"},
                    )
                if source_needs_key(source) and not values["aerodatabox_api_key"]:
                    return self.async_show_form(
                        step_id="init",
                        data_schema=global_schema(values, include_provider=True),
                        errors={"aerodatabox_api_key": "key_required"},
                    )
                values, found = await self._resolve_address(values, "home_address")
                if found:
                    return await self.async_step_confirm_address()
                return self.async_create_entry(title="", data=values)
            except AddressError as exc:
                errors["base"] = str(exc)
        defaults = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=global_schema(user_input or defaults, include_provider=True),
            errors=errors,
        )

    async def async_step_confirm_address(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=self._selected_address(user_input))
        return self._show_address_choice("confirm_address")

class BookingSubentryFlow(AddressSteps, config_entries.ConfigSubentryFlow):
    """Step 2 after setup: create a named itinerary with manual flight legs."""

    def __init__(self):
        self._setup_address()
        self._values = {}
        self._segments = []

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                name = (user_input.get("trip_name") or "").strip()
                if not (1 <= len(name) <= 80):
                    errors["trip_name"] = "invalid_name"
                else:
                    segment = make_segment(
                        user_input["flight_number"],
                        user_input["flight_date"],
                        user_input.get("departure_airport"),
                    )
                    margin = int(user_input["airport_margin_minutes"])
                    if not (45 <= margin <= 240 and margin % 15 == 0):
                        errors["airport_margin_minutes"] = "invalid_margin"
                    elif len((user_input.get("origin_address") or "").strip()) > 200:
                        errors["origin_address"] = "invalid_address"
                    else:
                        self._segments = [segment]
                        self._values = {
                            **user_input,
                            "trip_name": name,
                            "airline": airline_from_number(segment["flight_number"]),
                            "registration_mode": "manual",
                            "airport_margin_minutes": margin,
                        }
                        if user_input.get("has_stopover"):
                            return await self.async_step_flight()
                        return await self._complete_trip()
            except (KeyError, ValueError, TypeError):
                errors["base"] = "invalid_flight"
        return self.async_show_form(
            step_id="user", data_schema=booking_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_flight(self, user_input=None):
        """Add a connecting flight one by one, sharing the itinerary name."""
        errors = {}
        if user_input is not None:
            try:
                segment = make_segment(
                    user_input["flight_number"], user_input["flight_date"],
                    user_input.get("departure_airport"),
                )
                if segment in self._segments:
                    errors["base"] = "duplicate_flight"
                elif len(self._segments) >= 8:
                    errors["base"] = "too_many_flights"
                else:
                    self._segments.append(segment)
                    return await self.async_step_more_flights()
            except (ValueError, TypeError, KeyError):
                errors["base"] = "invalid_flight"
        return self.async_show_form(
            step_id="flight",
            data_schema=flight_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_more_flights(self, user_input=None):
        if user_input is not None:
            if user_input.get("add_another_flight") and len(self._segments) < 8:
                return await self.async_step_flight()
            return await self._complete_trip()
        return self.async_show_form(
            step_id="more_flights", data_schema=more_flights_schema(),
            description_placeholders={
                "segments": ", ".join(
                    f"{s['flight_number']} ({s['flight_date']})"
                    for s in self._segments
                ),
            },
        )

    async def _complete_trip(self):
        """Confirm address if supplied; otherwise use global HA origin."""
        data = {k: v for k, v in self._values.items() if k != "has_stopover"}
        data["segments"] = list(self._segments)
        data["booking_status"] = "pending_flight_lookup"
        address = (data.get("origin_address") or "").strip()
        data["origin_address"] = address
        if not address:
            data["departure_latitude"] = None
            data["departure_longitude"] = None
            data["resolved_origin_address"] = "Hjem / standardadresse"
            self._values = data
            return self._create_trip()
        try:
            self._pending = data
            self._matches = await lookup(self.hass, address)
        except AddressError as exc:
            return self.async_show_form(
                step_id="user",
                data_schema=booking_schema(self._values),
                errors={"base": str(exc)},
            )
        return await self.async_step_confirm_origin()

    async def async_step_confirm_origin(self, user_input=None):
        if user_input is not None:
            self._values = self._selected_address(
                user_input, field="origin_address"
            )
            return self._create_trip()
        return self._show_address_choice("confirm_origin")

    def _create_trip(self):
        """Manual flight dates are not verified passenger bookings."""
        return self.async_create_entry(
            title=self._values["trip_name"],
            data=self._values,
        )
