"""Unit tests for the integration setup and the renew_loans service handler."""

from __future__ import annotations

import pytest

from custom_components.bibliotek_dk import _make_renew_loans_handler
from custom_components.bibliotek_dk.const import (
    ATTR_LOAN_IDS,
    CONF_CONFIG_ENTRY_ID,
    DOMAIN,
)


class _FakeServices:
    def __init__(self):
        self.registered = {}
        self.removed = []

    def has_service(self, domain, service):
        return (domain, service) in self.registered

    def async_register(self, domain, service, handler):
        self.registered[(domain, service)] = handler

    def async_remove(self, domain, service):
        self.removed.append((domain, service))
        self.registered.pop((domain, service), None)


class _FakeConfigEntries:
    def __init__(self):
        self.reloaded = []

    async def async_reload(self, entry_id):
        self.reloaded.append(entry_id)
        return True


class _FakeHass:
    def __init__(self, library):
        self.data = {DOMAIN: {"entry1": library}}
        self.services = _FakeServices()
        self.config_entries = _FakeConfigEntries()

    async def async_add_executor_job(self, func, *args):
        return func(*args)


class _FakeCall:
    def __init__(self, data):
        self.data = data


class _FakeLibrary:
    def __init__(self, result=None):
        self.calls = []
        self._result = result

    def renewLoans(self, loan_ids):
        self.calls.append(loan_ids)
        if self._result is not None:
            return self._result
        return {loan_ids[0]: "renewed"}


@pytest.mark.asyncio
async def test_renew_loans_service_renews_and_reloads():
    library = _FakeLibrary()
    hass = _FakeHass(library)
    handler = _make_renew_loans_handler(hass)

    result = await handler(
        _FakeCall({CONF_CONFIG_ENTRY_ID: "entry1", ATTR_LOAN_IDS: ["7", "8"]})
    )

    assert library.calls == [["7", "8"]]
    assert result == {"renewed": {"7": "renewed"}}
    assert hass.config_entries.reloaded == ["entry1"]


@pytest.mark.asyncio
async def test_renew_loans_service_accepts_single_string_id():
    library = _FakeLibrary()
    hass = _FakeHass(library)
    handler = _make_renew_loans_handler(hass)

    await handler(_FakeCall({CONF_CONFIG_ENTRY_ID: "entry1", ATTR_LOAN_IDS: "7"}))

    assert library.calls == [["7"]]


@pytest.mark.asyncio
async def test_renew_loans_service_unknown_entry_raises():
    from homeassistant.exceptions import HomeAssistantError

    hass = _FakeHass(_FakeLibrary())
    handler = _make_renew_loans_handler(hass)

    with pytest.raises(HomeAssistantError):
        await handler(
            _FakeCall({CONF_CONFIG_ENTRY_ID: "missing", ATTR_LOAN_IDS: ["1"]})
        )


@pytest.mark.asyncio
async def test_renew_loans_service_no_ids_raises():
    from homeassistant.exceptions import HomeAssistantError

    hass = _FakeHass(_FakeLibrary())
    handler = _make_renew_loans_handler(hass)

    with pytest.raises(HomeAssistantError):
        await handler(_FakeCall({CONF_CONFIG_ENTRY_ID: "entry1", ATTR_LOAN_IDS: []}))


@pytest.mark.asyncio
async def test_renew_loans_service_raises_when_nothing_renewed():
    from homeassistant.exceptions import HomeAssistantError

    library = _FakeLibrary(result={1: "deniedReserved"})
    hass = _FakeHass(library)
    handler = _make_renew_loans_handler(hass)

    with pytest.raises(HomeAssistantError):
        await handler(_FakeCall({CONF_CONFIG_ENTRY_ID: "entry1", ATTR_LOAN_IDS: ["1"]}))
