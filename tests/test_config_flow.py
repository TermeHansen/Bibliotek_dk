"""Unit tests for config-flow input validation.

``validate_input`` is exercised with a fake ``hass`` and a monkeypatched
``Library`` so no network or Home Assistant runtime is needed.
"""
from __future__ import annotations

import pytest

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
