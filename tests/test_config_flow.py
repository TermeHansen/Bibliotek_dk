"""Unit tests for config-flow input validation.

``validate_input`` is exercised with a fake ``hass`` and a monkeypatched
``Library`` so no network or Home Assistant runtime is needed. The library-list
and municipality helpers are tested with a fake ``requests`` session.
"""

from __future__ import annotations

import json

import pytest
import requests

from custom_components.bibliotek_dk import config_flow
from custom_components.bibliotek_dk.const import (
    CONF_AGENCY,
    CONF_HOST,
    CONF_MUNICIPALITY,
    CONF_NAME,
    CONF_PINCODE,
    CONF_UPDATE_INTERVAL,
    CONF_USER_ID,
    DOMAIN,
    UPDATE_INTERVAL,
)
from custom_components.bibliotek_dk.crypto import (
    ENC_PREFIX_KEYFILE,
    decrypt_entry_credentials,
)


class FakeResponse:
    """Minimal stand-in for ``requests.Response``."""

    def __init__(self, text="", status_code=200, raise_exc=None):
        self.text = text
        self.status_code = status_code
        self._raise_exc = raise_exc

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self._raise_exc is not None:
            raise self._raise_exc


class FakeSession:
    """Fake session returning a preset response (or raising on get)."""

    def __init__(self, response=None, get_exc=None):
        self.headers = {}
        self._response = response
        self._get_exc = get_exc

    def get(self, url, **kwargs):
        if self._get_exc is not None:
            raise self._get_exc
        return self._response


class _FakeConfig:
    """Minimal hass.config exposing .path() for keyfile resolution."""

    def __init__(self, root):
        self._root = root

    def path(self, *parts):
        return str(self._root.joinpath(*parts))


class FakeHass:
    """Fake HomeAssistant exposing just what validate_input needs."""

    def __init__(self, tmp_path=None, data=None):
        self.data = data or {}
        self.config = _FakeConfig(tmp_path) if tmp_path is not None else None

    async def async_add_executor_job(self, func, *args):
        return func(*args)


LIBRARIES = {
    "Test Kommune": {
        CONF_HOST: "https://test.bib.dk",
        CONF_AGENCY: "710100",
    }
}


def _data(**overrides):
    base = {
        CONF_MUNICIPALITY: "Test Kommune",
        CONF_USER_ID: "1234-5678",
        CONF_PINCODE: "00 11",
        CONF_NAME: "Me",
        CONF_UPDATE_INTERVAL: 0,
    }
    base.update(overrides)
    return base


class _FakeSessionClose:
    def close(self):
        return None


@pytest.mark.asyncio
async def test_validate_input_success(tmp_path, monkeypatch):
    class FakeLibrary:
        def __init__(self, *args, **kwargs):
            self.user = type("U", (), {"userId": "12345678"})()
            self.session = _FakeSessionClose()

        def login(self):
            return "https://test.bib.dk/logout"

    monkeypatch.setattr(config_flow, "Library", FakeLibrary)
    hass = FakeHass(tmp_path)

    info = await config_flow.validate_input(hass, _data(), LIBRARIES)

    assert info["title"] == "Test Kommune (Me)"
    assert info["data"][CONF_HOST] == "https://test.bib.dk"
    assert info["data"][CONF_AGENCY] == "710100"
    assert info["data"][CONF_USER_ID].startswith(ENC_PREFIX_KEYFILE)
    assert info["data"][CONF_PINCODE].startswith(ENC_PREFIX_KEYFILE)
    # Non-digit characters are stripped before encryption.
    assert decrypt_entry_credentials(hass, info["data"], "710100") == (
        "12345678",
        "0011",
    )
    # A falsy interval falls back to the default.
    assert info["data"][CONF_UPDATE_INTERVAL] == UPDATE_INTERVAL


@pytest.mark.asyncio
async def test_validate_input_title_without_name(tmp_path, monkeypatch):
    class FakeLibrary:
        def __init__(self, *args, **kwargs):
            self.user = type("U", (), {"userId": "12345678"})()
            self.session = _FakeSessionClose()

        def login(self):
            return "https://test.bib.dk/logout"

    monkeypatch.setattr(config_flow, "Library", FakeLibrary)

    info = await config_flow.validate_input(
        FakeHass(tmp_path), _data(**{CONF_NAME: ""}), LIBRARIES
    )
    assert info["title"] == "Test Kommune"


@pytest.mark.asyncio
async def test_validate_input_invalid_auth(tmp_path, monkeypatch):
    class FakeLibrary:
        def __init__(self, *args, **kwargs):
            self.user = type("U", (), {"userId": "12345678"})()
            self.session = _FakeSessionClose()

        def login(self):
            return ""

    monkeypatch.setattr(config_flow, "Library", FakeLibrary)

    with pytest.raises(config_flow.InvalidAuth):
        await config_flow.validate_input(FakeHass(tmp_path), _data(), LIBRARIES)


@pytest.mark.asyncio
async def test_validate_input_duplicate_user(tmp_path, monkeypatch):
    existing = type(
        "Existing",
        (),
        {
            "user": type("U", (), {"userId": "12345678"})(),
            "host": "https://test.bib.dk",
        },
    )()
    hass = FakeHass(tmp_path, data={DOMAIN: {"entry1": existing}})

    with pytest.raises(config_flow.UserExist):
        await config_flow.validate_input(hass, _data(), LIBRARIES)


# --- refreshLibraries -------------------------------------------------------


def test_refresh_libraries_returns_the_api_list(monkeypatch):
    monkeypatch.setattr(
        config_flow,
        "_librariesFromDdf",
        lambda: {"Københavns Biblioteker": {CONF_AGENCY: "710100", CONF_HOST: "h"}},
    )

    libraries = config_flow.refreshLibraries()

    assert libraries == {
        "Københavns Biblioteker": {CONF_AGENCY: "710100", CONF_HOST: "h"}
    }


def test_refresh_libraries_http_error_raises_cannot_connect(monkeypatch):
    response = FakeResponse(
        status_code=500,
        raise_exc=requests.exceptions.HTTPError("boom"),
    )
    monkeypatch.setattr(
        config_flow.requests, "Session", lambda: FakeSession(response=response)
    )

    with pytest.raises(config_flow.CannotConnect):
        config_flow.refreshLibraries()


def test_refresh_libraries_timeout_raises_cannot_connect(monkeypatch):
    monkeypatch.setattr(
        config_flow.requests,
        "Session",
        lambda: FakeSession(get_exc=requests.exceptions.Timeout("slow")),
    )

    with pytest.raises(config_flow.CannotConnect):
        config_flow.refreshLibraries()


def test_refresh_libraries_raises_when_the_api_yields_nothing(monkeypatch):
    monkeypatch.setattr(config_flow, "_librariesFromDdf", lambda: None)

    with pytest.raises(config_flow.CannotConnect):
        config_flow.refreshLibraries()


# --- the JSON agency list ---------------------------------------------------


def _agency_payload():
    return {
        "data": [
            {
                "id": "710100",
                "name": "Københavns Biblioteker",
                "address": {"street": "Krystalgade 15"},
                "urlDomain": "bibliotek.kk.dk",
            },
            {
                "id": "763000",
                "name": "Vejle Bibliotekerne",
                "address": {"street": "Willy Sørensens Plads 1"},
                "urlDomain": "vejlebib.dk",
            },
        ]
    }


def test_libraries_from_ddf_maps_name_agency_and_host(monkeypatch):
    response = FakeResponse(text=json.dumps(_agency_payload()))
    monkeypatch.setattr(
        config_flow.requests, "Session", lambda: FakeSession(response=response)
    )

    libraries = config_flow._librariesFromDdf()

    assert libraries["Københavns Biblioteker"] == {
        CONF_AGENCY: "710100",
        CONF_HOST: "https://bibliotek.kk.dk",
    }
    assert libraries["Vejle Bibliotekerne"][CONF_HOST] == "https://vejlebib.dk"


def test_libraries_from_ddf_skips_entries_without_a_domain(monkeypatch):
    payload = {"data": [{"id": "710100", "name": "No Domain"}, {"name": "No Id"}]}
    response = FakeResponse(text=json.dumps(payload))
    monkeypatch.setattr(
        config_flow.requests, "Session", lambda: FakeSession(response=response)
    )

    # Nothing usable survived, so the caller must fall back rather than show an
    # empty dropdown.
    assert config_flow._librariesFromDdf() is None


def test_libraries_from_ddf_returns_none_on_http_error(monkeypatch):
    response = FakeResponse(
        status_code=401, raise_exc=requests.exceptions.HTTPError("unauthorized")
    )
    monkeypatch.setattr(
        config_flow.requests, "Session", lambda: FakeSession(response=response)
    )

    assert config_flow._librariesFromDdf() is None


def test_libraries_from_ddf_returns_none_on_unexpected_payload(monkeypatch):
    response = FakeResponse(text=json.dumps({"unexpected": True}))
    monkeypatch.setattr(
        config_flow.requests, "Session", lambda: FakeSession(response=response)
    )

    assert config_flow._librariesFromDdf() is None


def test_libraries_from_ddf_returns_none_when_data_is_null(monkeypatch):
    # A 200 with {"data": null} is a failed lookup, not an empty list: iterating
    # it used to raise TypeError out of setup instead of returning None.
    response = FakeResponse(text=json.dumps({"data": None}))
    monkeypatch.setattr(
        config_flow.requests, "Session", lambda: FakeSession(response=response)
    )

    assert config_flow._librariesFromDdf() is None


def test_libraries_from_ddf_skips_an_entry_without_an_id(monkeypatch):
    # name and urlDomain present but no id raised KeyError out of setup.
    payload = {
        "data": [
            {"name": "No Id", "urlDomain": "noid.dk"},
            {"id": "710100", "name": "Københavns Biblioteker", "urlDomain": "kk.dk"},
        ]
    }
    response = FakeResponse(text=json.dumps(payload))
    monkeypatch.setattr(
        config_flow.requests, "Session", lambda: FakeSession(response=response)
    )

    libraries = config_flow._librariesFromDdf()

    assert "No Id" not in libraries
    assert libraries["Københavns Biblioteker"][CONF_AGENCY] == "710100"


def test_libraries_from_ddf_skips_non_dict_entries(monkeypatch):
    payload = {"data": ["not-an-agency", {"id": "1", "name": "N", "urlDomain": "h.dk"}]}
    response = FakeResponse(text=json.dumps(payload))
    monkeypatch.setattr(
        config_flow.requests, "Session", lambda: FakeSession(response=response)
    )

    libraries = config_flow._librariesFromDdf()

    assert libraries["N"] == {CONF_AGENCY: "1", CONF_HOST: "https://h.dk"}
    # The two agencies the API omits are always added back.
    assert "Býarbókasavnið" in libraries
    assert "Kalaallit Nunaat" in libraries


def test_libraries_from_ddf_keeps_the_formerly_excluded_agencies(monkeypatch):
    # Assens, Odense, Svendborg, Nordfyns and Langeland were hidden because the
    # old scrape flagged them as gatewayf; the API has no such flag, so they are
    # listed like any other agency and users there can log in.
    payload = {
        "data": [
            {"id": "746100", "name": "Odense Bibliotekerne", "urlDomain": "odense.dk"},
            {"id": "710100", "name": "Københavns Biblioteker", "urlDomain": "kk.dk"},
        ]
    }
    response = FakeResponse(text=json.dumps(payload))
    monkeypatch.setattr(
        config_flow.requests, "Session", lambda: FakeSession(response=response)
    )

    libraries = config_flow._librariesFromDdf()

    assert libraries["Odense Bibliotekerne"] == {
        CONF_AGENCY: "746100",
        CONF_HOST: "https://odense.dk",
    }
    assert "Københavns Biblioteker" in libraries


def test_libraries_from_ddf_returns_none_when_nothing_usable(monkeypatch):
    # The two extra libraries must not turn an empty API answer into a
    # two-library list: the caller has to report cannot_connect.
    response = FakeResponse(text=json.dumps({"data": []}))
    monkeypatch.setattr(
        config_flow.requests, "Session", lambda: FakeSession(response=response)
    )

    assert config_flow._librariesFromDdf() is None


# --- resolveMunicipality ----------------------------------------------------


def _libs(*names):
    return {name: {CONF_AGENCY: str(i), CONF_HOST: "h"} for i, name in enumerate(names)}


def test_resolve_municipality_exact_match():
    libs = _libs("København", "Aarhus")
    assert config_flow.resolveMunicipality("København", libs) == "København"


def test_resolve_municipality_by_unique_substring():
    # The dropdown is keyed by agency name, so an exact lookup can never find
    # the municipality the reverse-geocode lookup returns.
    libs = _libs("Københavns Biblioteker", "Aarhus Bibliotekerne")
    assert (
        config_flow.resolveMunicipality("København", libs) == "Københavns Biblioteker"
    )
    assert config_flow.resolveMunicipality("Aarhus", libs) == "Aarhus Bibliotekerne"


def test_resolve_municipality_handles_danish_folding():
    # "Aa" is an older spelling of "å", so "Aalborg" finds "Aalborg Bibliotekerne"
    # and the folding also has to cope with æ/ø/å in the agency name.
    libs = _libs("Aalborg Bibliotekerne", "Århus Bibliotekerne")
    assert config_flow.resolveMunicipality("Aalborg", libs) == "Aalborg Bibliotekerne"
    assert config_flow.resolveMunicipality("Aarhus", libs) == "Århus Bibliotekerne"


def test_resolve_municipality_uses_the_thisted_alias():
    # The one municipality whose library name shares no substring with it:
    # DAWA says "Thisted", the API says "Biblioteket i Thy".
    libs = _libs("Biblioteket i Thy", "Københavns Biblioteker")
    assert config_flow.resolveMunicipality("Thisted", libs) == "Biblioteket i Thy"


def test_resolve_municipality_returns_none_when_ambiguous():
    # Guessing between two matches would silently pick the wrong library.
    libs = _libs("Nord Bibliotek", "Nordfyns Bibliotek")
    assert config_flow.resolveMunicipality("Nord", libs) is None


def test_resolve_municipality_returns_none_for_unknown_or_empty():
    libs = _libs("Københavns Biblioteker")
    assert config_flow.resolveMunicipality("Fanø", libs) is None
    assert config_flow.resolveMunicipality("", libs) is None


# --- municipalityFromCoor ---------------------------------------------------


def test_municipality_from_coor_returns_name(monkeypatch):
    response = FakeResponse(text=json.dumps({"navn": "København", "kode": "101"}))
    monkeypatch.setattr(
        config_flow.requests, "Session", lambda: FakeSession(response=response)
    )

    assert config_flow.municipalityFromCoor(12.5, 55.6) == "København"


def test_municipality_from_coor_missing_name_returns_empty(monkeypatch):
    response = FakeResponse(text=json.dumps({"kode": "101"}))
    monkeypatch.setattr(
        config_flow.requests, "Session", lambda: FakeSession(response=response)
    )

    assert config_flow.municipalityFromCoor(12.5, 55.6) == ""


def test_municipality_from_coor_http_error_returns_empty(monkeypatch):
    response = FakeResponse(
        status_code=500,
        raise_exc=requests.exceptions.HTTPError("boom"),
    )
    monkeypatch.setattr(
        config_flow.requests, "Session", lambda: FakeSession(response=response)
    )

    assert config_flow.municipalityFromCoor(12.5, 55.6) == ""
