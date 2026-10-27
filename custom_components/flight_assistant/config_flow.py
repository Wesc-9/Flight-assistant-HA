"""Home Assistant UI setup and options."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from .const import (
    DOMAIN, DEFAULT_MARGIN_MINUTES, DEFAULT_EXTRA_DRIVE_MINUTES,
)

def schema(defaults: dict) -> vol.Schema:
    return vol.Schema({
        vol.Required("home_latitude", default=defaults["home_latitude"]): vol.All(
            vol.Coerce(float), vol.Range(min=-90, max=90)),
        vol.Required("home_longitude", default=defaults["home_longitude"]): vol.All(
            vol.Coerce(float), vol.Range(min=-180, max=180)),
        vol.Required("airport_margin_minutes", default=defaults["airport_margin_minutes"]):
            vol.All(vol.Coerce(int), vol.Range(min=45, max=300)),
        vol.Required("drive_buffer_minutes", default=defaults["drive_buffer_minutes"]):
            vol.All(vol.Coerce(int), vol.Range(min=0, max=120)),
    })

class FlightAssistantConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="Flight Assistant", data=user_input)
        defaults = {
            "home_latitude": self.hass.config.latitude,
            "home_longitude": self.hass.config.longitude,
            "airport_margin_minutes": DEFAULT_MARGIN_MINUTES,
            "drive_buffer_minutes": DEFAULT_EXTRA_DRIVE_MINUTES,
        }
        return self.async_show_form(step_id="user", data_schema=schema(defaults))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return FlightAssistantOptionsFlow()

class FlightAssistantOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        defaults = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=schema(defaults))
