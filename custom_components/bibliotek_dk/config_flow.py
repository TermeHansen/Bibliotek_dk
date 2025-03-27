"""Config flow for Dummy Garage integration."""
from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector
from homeassistant import config_entries

from .library_api import Library
from bs4 import BeautifulSoup as BS
from typing import Any

import json
import logging
import re
import requests
import voluptuous as vol

from .const import (
    CONF_AGENCY,
    CONF_BRANCH_ID,
    CONF_HOST,
    CONF_MUNICIPALITY,
    CONF_NAME,
    CONF_PINCODE,
    CONF_SHOW_DEBTS,
    CONF_SHOW_LOANS,
    CONF_SHOW_ELOANS,
    CONF_SHOW_RESERVATIONS,
    CONF_UPDATE_INTERVAL,
    CONF_USER_ID,
    DOMAIN,
    HEADERS,
    MUNICIPALITY_LOOKUP_URL,
    UPDATE_INTERVAL,
    URL_FALLBACK,
    URL_LOGIN_PAGE,
)

_LOGGER = logging.getLogger(__name__)


async def validate_input(
    hass: HomeAssistant, data: dict[str, Any], libraries
) -> dict[str, Any]:

    # Retrieve HOST and UPDATE_INTERVAL
    data[CONF_HOST] = libraries[data[CONF_MUNICIPALITY]][CONF_HOST]
    data[CONF_AGENCY] = libraries[data[CONF_MUNICIPALITY]][CONF_AGENCY]
    data[CONF_UPDATE_INTERVAL] = (
        data[CONF_UPDATE_INTERVAL] if data[CONF_UPDATE_INTERVAL] else UPDATE_INTERVAL
    )

    # Typecast userId and Pincode to string:
    data[CONF_USER_ID] = re.sub(r"\D", "", data[CONF_USER_ID])
    data[CONF_PINCODE] = re.sub(r"\D", "", data[CONF_PINCODE])

    # If there is any other instances of the integration
    if DOMAIN in hass.data:
        # Test if the new user exist
        if any(
            libraryObj.user.userId == data[CONF_USER_ID]
            and libraryObj.host == data[CONF_HOST]
            for libraryObj in hass.data[DOMAIN].values()
        ):
            raise UserExist

    myLibrary = Library(data[CONF_USER_ID], data[CONF_PINCODE], data[CONF_HOST], data[CONF_AGENCY])
    # Try to login to test the credentails
    if not await hass.async_add_executor_job(myLibrary.login):
        raise InvalidAuth
    del myLibrary

    # Return info that you want to store in the config entry.
    title = (
        f"{data[CONF_MUNICIPALITY]} ({data[CONF_NAME]})"
        if data[CONF_NAME]
        else data[CONF_MUNICIPALITY]
    )
    return {"title": title, "data": data}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Dummy Garage."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlow:
        return OptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:

        # Fetch the list of libraries from a "fallback" loginpage
        def refreshLibraries() -> tuple:
            session = requests.Session()
            session.headers = HEADERS

            try:
                r = session.get(URL_FALLBACK + URL_LOGIN_PAGE)
                r.raise_for_status()

            except requests.exceptions.HTTPError as err:
                raise SystemExit(err) from err
            except requests.exceptions.Timeout:
                _LOGGER.error("Timeout fecthing (%s)", URL_FALLBACK + URL_LOGIN_PAGE)
            except requests.exceptions.TooManyRedirects:
                _LOGGER.error(
                    "Too many redirects fecthing (%s)", URL_FALLBACK + URL_LOGIN_PAGE
                )
            except requests.exceptions.RequestException as err:
                raise SystemExit(err) from err

            try:
                soup = BS(r.text, "html.parser")
                librariesJSON = json.loads(
                    soup.find(
                        "script",
                        text=re.compile(
                            r"^var libraries = (.)", re.MULTILINE | re.DOTALL
                        ),
                    ).string.replace("var libraries = ", "")
                )
            except (AttributeError, KeyError) as err:
                _LOGGER.error(
                    "Error loading the libraries from the fallback url. Error: %s", err
                )

            libraries, excLibraries = {}, {}
            if "folk" in librariesJSON.keys():
                for library in librariesJSON["folk"]:
                    p = re.compile(r"^.+?[^\/:](?=[?\/]|$)")
                    m = p.match(library["registrationUrl"])
                    # Only use libraries NOT using gatewayf
                    if "gatewayf" not in library["registrationUrl"]:
                        libraries[library[CONF_NAME]] = {
                            CONF_AGENCY: library[CONF_BRANCH_ID],
                            CONF_HOST: m.group(),
                        }
                    else:
                        excLibraries[library[CONF_NAME]] = {
                            CONF_AGENCY: library[CONF_BRANCH_ID],
                            CONF_HOST: m.group(),
                        }
            return libraries, excLibraries

        def municipalityFromCoor(lon, lat):
            session = requests.Session()
            session.headers = HEADERS

            try:
                r = session.get(
                    MUNICIPALITY_LOOKUP_URL.replace("LON", str(lon)).replace(
                        "LAT", str(lat)
                    )
                )
                r.raise_for_status()

            except requests.exceptions.HTTPError as err:
                _LOGGER.error(f"HTTP Error while fetching (%s): {err}", URL_FALLBACK + URL_LOGIN_PAGE)
                # Handle the error as needed, e.g., raise it, log it, or notify the user.
                return ""
            except requests.exceptions.Timeout:
                _LOGGER.error("Timeout fecthing (%s)", URL_FALLBACK + URL_LOGIN_PAGE)
            except requests.exceptions.TooManyRedirects:
                _LOGGER.error(
                    "Too many redirects fecthing (%s)", URL_FALLBACK + URL_LOGIN_PAGE
                )
            except requests.exceptions.RequestException as err:
                _LOGGER.error(f"Request Exception while fetching (%s): {err}", URL_FALLBACK + URL_LOGIN_PAGE)
                return ""

            municipality = json.loads(r.text)
            return municipality["navn"] if "navn" in municipality else ""

        # Async fetch list of "folk" libraries
        libraries, excLibraries = await self.hass.async_add_executor_job(
            refreshLibraries
        )

        errors = {}
        try:
            municipality = await self.hass.async_add_executor_job(
                municipalityFromCoor,
                self.hass.config.longitude,
                self.hass.config.latitude,
            )
            if municipality in excLibraries.keys():
                raise gatewayf
        except gatewayf:
            errors["base"] = "gatewayf"

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input, libraries)
            except UserExist:
                errors["base"] = "user_exist"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(user_input[CONF_USER_ID])
                # Abort the flow if a config entry with the same unique ID exists
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=info["title"], data=info["data"])

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_NAME, default=""): str,
                    vol.Required(
                        CONF_MUNICIPALITY, default=municipality
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=list(libraries.keys()),
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        ),
                    ),
                    vol.Required(CONF_USER_ID, default=""): str,
                    vol.Required(CONF_PINCODE, default=""): str,
                    vol.Required(CONF_SHOW_LOANS, default=True): bool,
                    vol.Required(CONF_SHOW_ELOANS, default=True): bool,
                    #                    vol.Required(CONF_SHOW_LOANS_OVERDUE, default=True): bool,
                    vol.Required(CONF_SHOW_DEBTS, default=True): bool,
                    vol.Required(CONF_SHOW_RESERVATIONS, default=True): bool,
                    #                    vol.Required(CONF_SHOW_RESERVATIONS_READY, default=True): bool,
                    vol.Optional(CONF_UPDATE_INTERVAL, default=UPDATE_INTERVAL): int,
                }
            ),
            errors=errors,
        )


class OptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:

        if user_input is not None:
            # update config entry
            new_data = {**self.config_entry.data, **user_input}
            return self.async_create_entry(
                title=self.config_entry.title, data=new_data
            )
        return self.async_show_form(
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({
                    vol.Required(CONF_SHOW_LOANS, default=True): bool,
                    vol.Required(CONF_SHOW_ELOANS, default=True): bool,
                    vol.Required(CONF_SHOW_DEBTS, default=True): bool,
                    vol.Required(CONF_SHOW_RESERVATIONS, default=True): bool,
                    vol.Optional(CONF_UPDATE_INTERVAL, default=UPDATE_INTERVAL): int,
                }),
                self.config_entry.options
            )
        )


class gatewayf(HomeAssistantError):
    """Error to indicate municipality is using gatewayf."""


class UserExist(HomeAssistantError):
    """Error to indicate user allready exist."""


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
