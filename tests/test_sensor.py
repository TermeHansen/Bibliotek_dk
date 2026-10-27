"""Unit tests for the sensor entities.

The entities only read attributes off a ``Library``/``libraryUser`` object and a
coordinator stub, so no Home Assistant machinery is required (see conftest.py).
"""

from __future__ import annotations

import datetime as dt

import pytest

from custom_components.bibliotek_dk import sensor as sensor_module
from custom_components.bibliotek_dk.const import (
    CONF_SHOW_DEBTS,
    CONF_SHOW_LOANS,
    CONF_SHOW_RESERVATIONS,
    CONF_UPDATE_INTERVAL,
    DOMAIN,
)
from custom_components.bibliotek_dk.library_api import Library
from custom_components.bibliotek_dk.models import (
    libraryDebt,
    libraryLoan,
    libraryMaterial,
    libraryReservation,
)
from custom_components.bibliotek_dk.sensor import (
    DebtSensor,
    LibrarySensor,
    LoanOverdueSensor,
    LoanSensor,
    ReservationReadySensor,
    ReservationSensor,
    md5_unique_id,
)


class FakeCoordinator:
    """Minimal coordinator exposing last_update_success."""

    def __init__(self, success=True):
        self.last_update_success = success


class FakeSetupCoordinator:
    """Coordinator stand-in for async_setup_entry."""

    def __init__(self, *args, **kwargs):
        self.last_update_success = True
        # The real coordinator exposes the update callable it was given.
        self.update_method = kwargs.get("update_method")

    async def async_request_refresh(self):
        return None

    def async_add_listener(self, cb):
        return lambda: None


class FakeEntry:
    def __init__(self, data):
        self.entry_id = "entry1"
        self.title = "Test Library"
        self.data = data
        self.options = {}


class FakeSetupHass:
    def __init__(self, library):
        self.data = {DOMAIN: {"entry1": library}}
        self.config = type("C", (), {"longitude": 0.0, "latitude": 0.0})()

    async def async_add_executor_job(self, func, *args):
        return func(*args)


def _library():
    lib = Library("1234", "0000", "https://example.invalid", "710100")
    lib.user.name = "Test User"
    lib.user.userId = "1234"
    lib.libraryName = "Test Library"
    return lib


def _loan(title, expire):
    mat = libraryMaterial(
        {
            "manifestation": {
                "pid": "p",
                "ownerWork": {"workId": "work-of:x"},
                "titles": {"full": [title]},
                "creators": [{"display": "Author"}],
                "cover": {"thumbnail": "c"},
                "materialTypes": [{"materialTypeSpecific": {"display": "bog"}}],
            }
        }
    )
    loan = libraryLoan.__new__(libraryLoan)
    loan.__dict__.update(mat.__dict__)
    loan.expireDate = expire
    return loan


def test_md5_unique_id_is_stable_and_hex():
    a = md5_unique_id("Loans_1234")
    b = md5_unique_id("Loans_1234")
    assert a == b
    assert len(a) == 32
    assert a != md5_unique_id("Loans_5678")


def test_library_sensor_state_days_to_return():
    lib = _library()
    today = dt.datetime.now()
    lib.user.loans = [_loan("First", today + dt.timedelta(days=5))]
    lib.user.loansOverdue = []
    lib.user.reservations = []
    lib.user.reservationsReady = []
    lib.user.debts = []

    sensor = LibrarySensor(lib, FakeCoordinator())
    assert sensor.state == 5
    attrs = sensor.extra_state_attributes
    assert attrs["loans"] == 1
    assert attrs["sensor_type"] == "main"


def test_library_sensor_state_empty_without_loans():
    lib = _library()
    lib.user.loans = []
    sensor = LibrarySensor(lib, FakeCoordinator())
    assert sensor.state == ""


def test_loan_sensor_state_and_attributes():
    lib = _library()
    lib.user.loans = [_loan("A", dt.datetime(2026, 1, 1))]
    sensor = LoanSensor(lib.user, FakeCoordinator())
    assert sensor.state == 1
    assert sensor.extra_state_attributes["loans"][0]["title"] == "A"


def test_debt_sensor_sums_amounts():
    lib = _library()
    d1 = libraryDebt.__new__(libraryDebt)
    d1.__dict__.update(libraryMaterial({}).__dict__)
    d1.feeAmount = "10.50"
    d1.feeType = "overdue"
    d2 = libraryDebt.__new__(libraryDebt)
    d2.__dict__.update(libraryMaterial({}).__dict__)
    d2.feeAmount = "4.50"
    d2.feeType = "replacement"
    lib.user.debts = [d1, d2]
    lib.user.debtsAmount = 15.0

    sensor = DebtSensor(lib.user, FakeCoordinator())
    assert sensor.state == 15.0
    assert sensor.extra_state_attributes["debts"][0]["fee_type"] == "overdue"


def test_debt_sensor_is_monetary():
    from decimal import Decimal

    lib = _library()
    lib.user.debts = []
    lib.user.debtsAmount = 12.5
    sensor = DebtSensor(lib.user, FakeCoordinator())
    assert sensor._attr_device_class == "monetary"
    assert sensor._attr_native_unit_of_measurement == "DKK"
    assert sensor._attr_state_class == "total"
    assert sensor.native_value == Decimal("12.5")


def test_loan_sensor_exposes_renew_id():
    lib = _library()
    loan = _loan("A", dt.datetime(2026, 1, 1))
    loan.renewId = 42
    lib.user.loans = [loan]
    sensor = LoanSensor(lib.user, FakeCoordinator())
    assert sensor.extra_state_attributes["loans"][0]["renew_id"] == 42


def test_reservation_sensor_state():
    lib = _library()
    res = libraryReservation.__new__(libraryReservation)
    res.__dict__.update(libraryMaterial({}).__dict__)
    res.title = "R"
    lib.user.reservations = [res]
    sensor = ReservationSensor(lib.user, FakeCoordinator())
    assert sensor.state == 1


def test_sensor_availability_follows_coordinator():
    lib = _library()
    sensor = LibrarySensor(lib, FakeCoordinator(success=False))
    assert sensor.available is False
    sensor = LibrarySensor(lib, FakeCoordinator(success=True))
    assert sensor.available is True


def _entry_data(**overrides):
    base = {
        CONF_SHOW_LOANS: True,
        CONF_SHOW_DEBTS: True,
        CONF_SHOW_RESERVATIONS: True,
        CONF_UPDATE_INTERVAL: 60,
    }
    base.update(overrides)
    return base


async def _setup(monkeypatch, **overrides):
    captured = {}

    def fake_add(entities):
        captured["entities"] = entities

    def fake_coordinator(*args, **kwargs):
        coordinator = FakeSetupCoordinator(*args, **kwargs)
        captured["coordinator"] = coordinator
        return coordinator

    monkeypatch.setattr(sensor_module, "DataUpdateCoordinator", fake_coordinator)
    lib = _library()
    hass = FakeSetupHass(lib)
    entry = FakeEntry(_entry_data(**overrides))
    await sensor_module.async_setup_entry(hass, entry, fake_add)
    return captured.get("entities", []), captured.get("coordinator")


@pytest.mark.asyncio
async def test_setup_creates_all_sensors_when_enabled(monkeypatch):
    entities, _ = await _setup(monkeypatch)
    types = {type(e).__name__ for e in entities}
    assert types == {
        "LibrarySensor",
        "LoanSensor",
        "LoanOverdueSensor",
        "DebtSensor",
        "ReservationSensor",
        "ReservationReadySensor",
    }


@pytest.mark.asyncio
async def test_setup_groups_sensors_under_one_device(monkeypatch):
    entities, _ = await _setup(monkeypatch)
    infos = [e._attr_device_info for e in entities]
    assert all(info is not None for info in infos)
    # One device per config entry: identical identifiers on every sensor.
    assert len({tuple(info["identifiers"]) for info in infos}) == 1
    assert ("bibliotek_dk", "entry1") in infos[0]["identifiers"]
    assert infos[0]["name"] == "Test Library"


@pytest.mark.asyncio
async def test_setup_hides_disabled_sensor_groups(monkeypatch):
    # Regression: these gates were `if True`, so the options did nothing.
    entities, _ = await _setup(
        monkeypatch,
        **{
            CONF_SHOW_LOANS: False,
            CONF_SHOW_DEBTS: False,
            CONF_SHOW_RESERVATIONS: False,
        },
    )
    types = {type(e).__name__ for e in entities}
    assert types == {"LibrarySensor"}
    assert isinstance(entities[0], LibrarySensor)


@pytest.mark.asyncio
async def test_setup_only_loans_enabled(monkeypatch):
    entities, _ = await _setup(
        monkeypatch,
        **{CONF_SHOW_DEBTS: False, CONF_SHOW_RESERVATIONS: False},
    )
    types = {type(e).__name__ for e in entities}
    assert types == {"LibrarySensor", "LoanSensor", "LoanOverdueSensor"}
    assert any(isinstance(e, LoanOverdueSensor) for e in entities)
    assert not any(
        isinstance(e, (DebtSensor, ReservationSensor, ReservationReadySensor))
        for e in entities
    )


@pytest.mark.asyncio
async def test_setup_maps_auth_failure_to_update_failed(monkeypatch):
    # Regression (plan 7.1): a failed login must surface as UpdateFailed so the
    # coordinator marks the sensors unavailable instead of serving stale data.
    from homeassistant.helpers.update_coordinator import UpdateFailed

    from custom_components.bibliotek_dk.library_api import LibraryAuthError

    entities, coordinator = await _setup(monkeypatch)
    assert entities

    def failing_update(self):
        raise LibraryAuthError("Login failed for 1234")

    monkeypatch.setattr(Library, "update", failing_update)
    with pytest.raises(UpdateFailed):
        await coordinator.update_method()

    # Any other error must pass through unchanged, not be swallowed.
    def other_update(self):
        raise RuntimeError("boom")

    monkeypatch.setattr(Library, "update", other_update)
    with pytest.raises(RuntimeError):
        await coordinator.update_method()
