"""Unit tests for the diagnostics handler (secrets must be redacted)."""

from __future__ import annotations

import pytest

from custom_components.bibliotek_dk.const import (
    CONF_PINCODE,
    CONF_USER_ID,
    DOMAIN,
)
from custom_components.bibliotek_dk.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.bibliotek_dk.library_api import Library


class _FakeEntry:
    def __init__(self, data):
        self.entry_id = "entry1"
        self.data = data
        self.options = {}


class _FakeHass:
    def __init__(self, library):
        self.data = {DOMAIN: {"entry1": library}}


def _library():
    lib = Library("1234567890", "4321", "https://example.invalid", "710100")
    lib.user.name = "Test User"
    lib.user.pickupLibrary = "Test Branch"
    lib.user.debtsAmount = 12.5
    return lib


@pytest.mark.asyncio
async def test_diagnostics_redacts_credentials():
    hass = _FakeHass(_library())
    entry = _FakeEntry({CONF_USER_ID: "1234567890", CONF_PINCODE: "4321"})

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert diag["entry"][CONF_USER_ID] == "**REDACTED**"
    assert diag["entry"][CONF_PINCODE] == "**REDACTED**"
    assert diag["account"]["name"] == "Test User"
    assert diag["account"]["debts_amount"] == 12.5
    assert diag["account"]["counts"]["loans"] == 0


@pytest.mark.asyncio
async def test_diagnostics_handles_missing_library():
    hass = _FakeHass(None)
    hass.data = {DOMAIN: {}}
    entry = _FakeEntry({CONF_USER_ID: "1", CONF_PINCODE: "2"})

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert diag["account"]["name"] is None
