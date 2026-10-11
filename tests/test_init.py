"""Unit tests for the integration setup and the renew_loans service handler."""

from __future__ import annotations

import pytest

from custom_components.bibliotek_dk import (
    _make_renew_loans_handler,
    _normalize_loan_ids,
)
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

    def async_register(self, domain, service, handler, supports_response=None):
        self.registered[(domain, service)] = handler
        self.registered[("_response", domain, service)] = supports_response

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


class _FakeCoordinator:
    def __init__(self):
        self.refreshes = 0

    async def async_request_refresh(self):
        self.refreshes += 1


class _FakeLibrary:
    def __init__(self, result=None):
        self.calls = []
        self._result = result
        self.coordinator = _FakeCoordinator()

    def renewLoans(self, loan_ids):
        self.calls.append(loan_ids)
        if self._result is not None:
            return self._result
        return {loan_ids[0]: "renewed"}


@pytest.mark.asyncio
async def test_renew_loans_service_renews_and_refreshes():
    library = _FakeLibrary()
    hass = _FakeHass(library)
    handler = _make_renew_loans_handler(hass)

    result = await handler(
        _FakeCall({CONF_CONFIG_ENTRY_ID: "entry1", ATTR_LOAN_IDS: ["7", "8"]})
    )

    # Ids are normalized to ints for the API.
    assert library.calls == [[7, 8]]
    assert result == {"renewed": {7: "renewed"}}
    # The existing coordinator is refreshed; the entry is not rebuilt.
    assert library.coordinator.refreshes == 1
    assert hass.config_entries.reloaded == []


@pytest.mark.asyncio
async def test_renew_loans_service_accepts_single_string_id():
    library = _FakeLibrary()
    hass = _FakeHass(library)
    handler = _make_renew_loans_handler(hass)

    await handler(_FakeCall({CONF_CONFIG_ENTRY_ID: "entry1", ATTR_LOAN_IDS: "7"}))

    assert library.calls == [[7]]


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

    # A denied renewal must not refresh or rebuild anything.
    assert library.coordinator.refreshes == 0
    assert hass.config_entries.reloaded == []


class _FakeSetupEntry:
    def __init__(self):
        self.entry_id = "entry1"
        self.title = "Test"
        self.data = {
            "user_id": "1234",
            "pincode": "0000",
            "host": "https://example.invalid",
            "agency": "710100",
            "municipality": "Test",
        }
        self.options = {}

    def async_on_unload(self, func):
        return None

    def add_update_listener(self, func):
        return func


class _FakeSetupHass:
    def __init__(self):
        self.data = {}
        self.services = _FakeServices()
        self.config_entries = _FakeSetupConfigEntries()

    async def async_add_executor_job(self, func, *args):
        return func(*args)


class _FakeSetupConfigEntries:
    async def async_forward_entry_setups(self, entry, platforms):
        return True


@pytest.mark.asyncio
async def test_setup_registers_service_with_response_support(monkeypatch):
    from custom_components.bibliotek_dk import async_setup_entry
    from custom_components.bibliotek_dk.const import SERVICE_RENEW_LOANS

    hass = _FakeSetupHass()
    entry = _FakeSetupEntry()

    assert await async_setup_entry(hass, entry) is True

    key = (DOMAIN, SERVICE_RENEW_LOANS)
    assert key in hass.services.registered
    # Without supports_response, response_variable is rejected before the handler.
    assert hass.services.registered[("_response", *key)] == "optional"


def test_normalize_loan_ids_rejects_non_integers():
    from homeassistant.exceptions import HomeAssistantError

    assert _normalize_loan_ids(["1", 2]) == [1, 2]
    with pytest.raises(HomeAssistantError):
        _normalize_loan_ids(["123x"])
    with pytest.raises(HomeAssistantError):
        _normalize_loan_ids([None])
    with pytest.raises(HomeAssistantError):
        _normalize_loan_ids([])
