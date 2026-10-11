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
    CONF_BRANCH_ID,
    CONF_HOST,
    CONF_MUNICIPALITY,
    CONF_NAME,
    CONF_PINCODE,
    CONF_UPDATE_INTERVAL,
    CONF_USER_ID,
    DOMAIN,
    UPDATE_INTERVAL,
)


class FakeResponse:
    """Minimal stand-in for ``requests.Response``."""

    def __init__(self, text="", status_code=200, raise_exc=None):
        self.text = text
        self.status_code = status_code
        self._raise_exc = raise_exc

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


def _fallback_page(folk):
    """Build a fallback login page embedding ``var libraries = ...``."""
    payload = json.dumps({"folk": folk})
    return f"<html><script>var libraries = {payload}</script></html>"


class FakeHass:
    """Fake HomeAssistant exposing just what validate_input needs."""

    def __init__(self, data=None):
        self.data = data or {}

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


@pytest.mark.asyncio
async def test_validate_input_success(monkeypatch):
    class FakeLibrary:
        def __init__(self, *args, **kwargs):
            self.user = type("U", (), {"userId": "12345678"})()

        def login(self):
            return "https://test.bib.dk/logout"

    monkeypatch.setattr(config_flow, "Library", FakeLibrary)

    info = await config_flow.validate_input(FakeHass(), _data(), LIBRARIES)

    assert info["title"] == "Test Kommune (Me)"
    assert info["data"][CONF_HOST] == "https://test.bib.dk"
    assert info["data"][CONF_AGENCY] == "710100"
    # Non-digit characters are stripped from user id and pincode.
    assert info["data"][CONF_USER_ID] == "12345678"
    assert info["data"][CONF_PINCODE] == "0011"
    # A falsy interval falls back to the default.
    assert info["data"][CONF_UPDATE_INTERVAL] == UPDATE_INTERVAL


@pytest.mark.asyncio
async def test_validate_input_title_without_name(monkeypatch):
    class FakeLibrary:
        def __init__(self, *args, **kwargs):
            self.user = type("U", (), {"userId": "12345678"})()

        def login(self):
            return "https://test.bib.dk/logout"

    monkeypatch.setattr(config_flow, "Library", FakeLibrary)

    info = await config_flow.validate_input(
        FakeHass(), _data(**{CONF_NAME: ""}), LIBRARIES
    )
    assert info["title"] == "Test Kommune"


@pytest.mark.asyncio
async def test_validate_input_invalid_auth(monkeypatch):
    class FakeLibrary:
        def __init__(self, *args, **kwargs):
            self.user = type("U", (), {"userId": "12345678"})()

        def login(self):
            return ""

    monkeypatch.setattr(config_flow, "Library", FakeLibrary)

    with pytest.raises(config_flow.InvalidAuth):
        await config_flow.validate_input(FakeHass(), _data(), LIBRARIES)


@pytest.mark.asyncio
async def test_validate_input_duplicate_user(monkeypatch):
    existing = type(
        "Existing",
        (),
        {
            "user": type("U", (), {"userId": "12345678"})(),
            "host": "https://test.bib.dk",
        },
    )()
    hass = FakeHass(data={DOMAIN: {"entry1": existing}})

    with pytest.raises(config_flow.UserExist):
        await config_flow.validate_input(hass, _data(), LIBRARIES)


# --- refreshLibraries -------------------------------------------------------


def test_refresh_libraries_parses_and_splits_gatewayf(monkeypatch):
    folk = [
        {
            CONF_NAME: "Test Kommune",
            CONF_BRANCH_ID: "710100",
            "registrationUrl": "https://test.bib.dk/login?current-path=/user/me/dashboard",
        },
        {
            CONF_NAME: "Gateway Kommune",
            CONF_BRANCH_ID: "720000",
            "registrationUrl": "https://gatewayf.example.dk/login",
        },
    ]
    response = FakeResponse(text=_fallback_page(folk))
    monkeypatch.setattr(
        config_flow.requests, "Session", lambda: FakeSession(response=response)
    )

    libraries, exc = config_flow.refreshLibraries()

    assert libraries["Test Kommune"][CONF_HOST] == "https://test.bib.dk"
    assert libraries["Test Kommune"][CONF_AGENCY] == "710100"
    assert "Gateway Kommune" in exc
    assert "Gateway Kommune" not in libraries


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


def test_refresh_libraries_missing_script_returns_empty(monkeypatch):
    response = FakeResponse(text="<html>no script here</html>")
    monkeypatch.setattr(
        config_flow.requests, "Session", lambda: FakeSession(response=response)
    )

    assert config_flow.refreshLibraries() == ({}, {})


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
