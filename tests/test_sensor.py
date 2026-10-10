"""Unit tests for the sensor entities.

The entities only read attributes off a ``Library``/``libraryUser`` object and a
coordinator stub, so no Home Assistant machinery is required (see conftest.py).
"""
from __future__ import annotations

import datetime as dt

from custom_components.bibliotek_dk.library_api import (
    Library,
    libraryDebt,
    libraryLoan,
    libraryMaterial,
    libraryReservation,
)
from custom_components.bibliotek_dk.sensor import (
    DebtSensor,
    LibrarySensor,
    LoanSensor,
    ReservationSensor,
    md5_unique_id,
)


class FakeCoordinator:
    """Minimal coordinator exposing last_update_success."""

    def __init__(self, success=True):
        self.last_update_success = success


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
