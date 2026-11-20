"""Config flow for Bibliotek integration."""

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
    DDF_API_KEY,
    DDF_APP_TYPE,
    DDF_APP_VERSION,
    DDF_INSTALLATION_ID,
    DOMAIN,
    EXTRA_LIBRARIES,
    GATEWAYF_AGENCIES,
    HEADERS,
    MUNICIPALITY_ALIASES,
    MUNICIPALITY_LOOKUP_URL,
    UPDATE_INTERVAL,
    URL_DDF_BASE,
    URL_FALLBACK,
    URL_LOGIN_PAGE,
)

_LOGGER = logging.getLogger(__name__)

# Timeout (seconds) applied to the library-list and municipality lookups.
REQUEST_TIMEOUT = 10


def _ddfSession():
    """A session carrying the Biblo app's client-hint headers."""
    session = requests.Session()
    session.headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate",
        "x-app": DDF_APP_VERSION,
        "x-app-type": DDF_APP_TYPE,
        "x-installation-id": DDF_INSTALLATION_ID,
        "x-api-key": DDF_API_KEY,
    }
    return session


def _librariesFromDdf():
    """Fetch the agency list as JSON from the Biblo app API.

    Returns a ``{name: {agency, host}}`` mapping, or ``None`` when the API is
    unreachable or answers something unexpected, so the caller can fall back to
    the scraped login page.

    The API lists agencies by their own names ("Københavns Biblioteker") rather
    than by municipality ("København"), and it carries no gatewayf flag, so the
    exclusions are applied from ``GATEWAYF_AGENCIES`` and the municipality is
    resolved separately by ``resolveMunicipality``.
    """
    try:
        r = _ddfSession().get(f"{URL_DDF_BASE}/v1/agencies", timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        agencies = r.json()["data"]
    except (
        requests.exceptions.RequestException,
        KeyError,
        TypeError,
        ValueError,
    ) as err:
        _LOGGER.warning(f"Could not load libraries from the Biblo API: {err}")
        return None

    # A 200 with `{"data": null}` is a failed lookup, not an empty list: iterate
    # only a real sequence, and skip any entry missing an id rather than raising.
    if not isinstance(agencies, list):
        _LOGGER.warning(f"Unexpected agency payload from the Biblo API: {agencies!r}")
        return None

    libraries = {}
    for agency in agencies:
        if not isinstance(agency, dict):
            continue
        name, domain, agency_id = (
            agency.get("name"),
            agency.get("urlDomain"),
            agency.get("id"),
        )
        if not name or not domain or not agency_id:
            continue
        if str(agency_id) in GATEWAYF_AGENCIES:
            continue
        libraries[name] = {
            CONF_AGENCY: str(agency_id),
            CONF_HOST: f"https://{domain}",
        }

    # An empty result means the API answered but carried nothing usable, which
    # is a failure, not a two-library list: return None so the caller falls back
    # to the scraped page instead of showing a near-empty dropdown.
    if not libraries:
        return None

    # The API omits the Faroese and Greenlandic agencies, which the integration
    # supports, so add them back explicitly rather than losing them silently.
    for agency_id, name, host in EXTRA_LIBRARIES:
        libraries.setdefault(name, {CONF_AGENCY: agency_id, CONF_HOST: host})

    return libraries


def _librariesFromPage() -> tuple[dict, dict] | None:
    """Scrape the login page's ``var libraries`` script.

    Returns ``(libraries, excLibraries)`` or ``None`` when the page cannot be
    fetched or parsed, so the caller can fall back to the API.
    """
    session = requests.Session()
    session.headers = HEADERS

    try:
        r = session.get(URL_FALLBACK + URL_LOGIN_PAGE, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
    except requests.exceptions.RequestException as err:
        _LOGGER.warning(f"Could not load the library list from the login page: {err}")
        return None

    try:
        soup = BS(r.text, "html.parser")
        librariesJSON = json.loads(
            soup.find(
                "script",
                string=re.compile(r"^var libraries = (.)", re.MULTILINE | re.DOTALL),
            ).string.replace("var libraries = ", "")
        )
    except (AttributeError, KeyError, ValueError) as err:
        # AttributeError/KeyError: the script is gone or reshaped. ValueError
        # covers json.JSONDecodeError, so a script that is still present but no
        # longer valid JSON (e.g. a trailing semicolon) also falls back.
        _LOGGER.warning(f"Could not parse the library list from the login page: {err}")
        return None

    libraries, excLibraries = {}, {}
    for library in librariesJSON.get("folk", []):
        p = re.compile(r"^.+?[^\/:](?=[?\/]|$)")
        m = p.match(library["registrationUrl"])
        entry = {
            CONF_AGENCY: library[CONF_BRANCH_ID],
            CONF_HOST: m.group() if m else "",
        }
        if "gatewayf" in library["registrationUrl"]:
            excLibraries[library[CONF_NAME]] = entry
        else:
            libraries[library[CONF_NAME]] = entry
    return libraries, excLibraries


def refreshLibraries() -> tuple[dict, dict]:
    """Build the ``{name: {agency, host}}`` library list for the config flow.

    The API's agency list is the primary source; the scraped login page is the
    fallback, because it is a single page whose script can be redesigned away.
    Returns ``(libraries, excLibraries)``, where the second maps the libraries
    hidden because they use gatewayf/WAYF logins.
    """
    libraries = _librariesFromDdf()
    if libraries is not None:
        return libraries, _excludedLibraries()

    page = _librariesFromPage()
    if page is not None:
        return page

    # Neither source worked: raise so the flow reports cannot_connect rather
    # than showing an empty dropdown.
    raise CannotConnect


def _excludedLibraries() -> dict:
    """Name the agencies hidden by ``GATEWAYF_AGENCIES``.

    The API list carries no gatewayf flag, so the exclusion is by agency id and
    the names have to be fetched separately to explain the gap to a user whose
    municipality is affected. Best effort: a failure here must not break the
    flow, so it only costs the explanatory message.
    """
    try:
        r = _ddfSession().get(f"{URL_DDF_BASE}/v1/agencies", timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        agencies = r.json()["data"]
    except (
        requests.exceptions.RequestException,
        KeyError,
        TypeError,
        ValueError,
    ) as err:
        _LOGGER.debug(f"Could not name the excluded agencies: {err}")
        return {}

    if not isinstance(agencies, list):
        return {}

    excluded = {}
    for agency in agencies:
        if not isinstance(agency, dict):
            continue
        agency_id = agency.get("id")
        name, domain = agency.get("name"), agency.get("urlDomain")
        if agency_id and name and str(agency_id) in GATEWAYF_AGENCIES:
            excluded[name] = {
                CONF_AGENCY: str(agency_id),
                CONF_HOST: f"https://{domain}" if domain else "",
            }
    return excluded


def _fold(text: str) -> str:
    """Lower-case and strip Danish diacritics, for name matching only."""
    folded = text.lower().translate(
        str.maketrans({"æ": "ae", "ø": "o", "å": "a", "ö": "o", "ä": "a", "é": "e"})
    )
    # "Aa" is an older spelling of "å", so treat both spellings as one.
    return folded.replace("aa", "a")


def resolveMunicipality(municipality: str, libraries: dict) -> str | None:
    """Map a municipality name onto a key of ``libraries``.

    The dropdown is keyed by agency name ("Københavns Biblioteker") while the
    reverse-geocode lookup returns the municipality ("København"), so an exact
    dict lookup can never preselect the right entry. Resolution order:

    1. an exact name match,
    2. an explicit ``MUNICIPALITY_ALIASES`` entry,
    3. the single library whose folded name contains the folded municipality.

    Returns ``None`` when nothing matches or the substring match is ambiguous,
    so the caller keeps its own default rather than guessing.
    """
    if not municipality:
        return None
    if municipality in libraries:
        return municipality

    alias = MUNICIPALITY_ALIASES.get(municipality)
    if alias in libraries:
        return alias

    wanted = _fold(municipality)
    matches = [name for name in libraries if wanted in _fold(name)]
    return matches[0] if len(matches) == 1 else None


def municipalityFromCoor(lon, lat) -> str:
    """Reverse-lookup the municipality name from a (lon, lat) coordinate."""
    session = requests.Session()
    session.headers = HEADERS

    try:
        r = session.get(
            MUNICIPALITY_LOOKUP_URL.replace("LON", str(lon)).replace("LAT", str(lat)),
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()

    except requests.exceptions.HTTPError as err:
        _LOGGER.error(f"HTTP Error while fetching municipality: {err}")
        return ""
    except requests.exceptions.Timeout as err:
        _LOGGER.error(f"Timeout fecthing municipality: {err}")
        return ""
    except requests.exceptions.TooManyRedirects as err:
        _LOGGER.error(f"Too many redirects fecthing municipality: {err}")
        return ""
    except requests.exceptions.RequestException as err:
        _LOGGER.error(f"Request Exception while fetching municipality: {err}")
        return ""

    municipality = json.loads(r.text)
    return municipality["navn"] if "navn" in municipality else ""


async def validate_input(
    hass: HomeAssistant, data: dict[str, Any], libraries
) -> dict[str, Any]:

    # The dropdown is keyed by agency name, which is not always the municipality
    # ("København" -> "Københavns Biblioteker"), so resolve before looking up.
    key = resolveMunicipality(data[CONF_MUNICIPALITY], libraries)
    if key is None:
        # Answering a value that is not in the list (a hand-crafted request, or
        # an agency that disappeared between showing the form and submitting it).
        # Reported as "unknown" by the flow, like the KeyError this replaced.
        raise ValueError(f"Unknown library {data[CONF_MUNICIPALITY]!r}")

    # Retrieve HOST and UPDATE_INTERVAL
    data[CONF_HOST] = libraries[key][CONF_HOST]
    data[CONF_AGENCY] = libraries[key][CONF_AGENCY]
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

    myLibrary = Library(
        data[CONF_USER_ID], data[CONF_PINCODE], data[CONF_HOST], data[CONF_AGENCY]
    )
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
    """Handle a config flow for Bibliotek."""

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

        errors = {}

        # Async fetch list of "folk" libraries
        try:
            libraries, excLibraries = await self.hass.async_add_executor_job(
                refreshLibraries
            )
        except CannotConnect:
            errors["base"] = "cannot_connect"
            libraries, excLibraries = {}, {}

        municipality = ""
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

        # The dropdown is keyed by agency name, so preselect the resolved key
        # rather than the raw municipality (which would be an invalid default
        # for the 13 municipalities whose library is named differently).
        defaultLibrary = resolveMunicipality(municipality, libraries)

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
                        CONF_MUNICIPALITY,
                        default=defaultLibrary or vol.UNDEFINED,
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=list(libraries.keys()),
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        ),
                    ),
                    vol.Required(CONF_USER_ID, default=""): str,
                    vol.Required(CONF_PINCODE, default=""): str,
                    vol.Optional(CONF_SHOW_LOANS, default=True): bool,
                    vol.Optional(CONF_SHOW_ELOANS, default=True): bool,
                    #                    vol.Required(CONF_SHOW_LOANS_OVERDUE, default=True): bool,
                    vol.Optional(CONF_SHOW_DEBTS, default=True): bool,
                    vol.Optional(CONF_SHOW_RESERVATIONS, default=True): bool,
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
            return self.async_create_entry(title=self.config_entry.title, data=new_data)
        data = self.config_entry.data
        options_schema = vol.Schema(
            {
                vol.Optional(CONF_SHOW_LOANS, default=data[CONF_SHOW_LOANS]): bool,
                vol.Optional(
                    CONF_SHOW_ELOANS, default=data.get(CONF_SHOW_ELOANS, True)
                ): bool,
                vol.Optional(CONF_SHOW_DEBTS, default=data[CONF_SHOW_DEBTS]): bool,
                vol.Optional(
                    CONF_SHOW_RESERVATIONS, default=data[CONF_SHOW_RESERVATIONS]
                ): bool,
                vol.Optional(
                    CONF_UPDATE_INTERVAL, default=data[CONF_UPDATE_INTERVAL]
                ): int,
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                options_schema, self.config_entry.options
            ),
        )


class gatewayf(HomeAssistantError):
    """Error to indicate municipality is using gatewayf."""


class UserExist(HomeAssistantError):
    """Error to indicate user allready exist."""


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
