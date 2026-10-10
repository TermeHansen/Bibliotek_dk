"""Unit tests for the Library client parsing logic.

These use small, anonymised fixtures shaped like the real DBC responses
(see ``tests/data/``) and never touch the network.
"""
from __future__ import annotations

import datetime as dt

from custom_components.bibliotek_dk.library_api import (
    Library,
    libraryDebt,
    libraryLoan,
    libraryMaterial,
    libraryReservation,
    libraryReservationReady,
)


def _manifestation(pid="870970-basis:123", work_id="work-of:870970-basis:123"):
    """Build a physical-manifestation details payload."""
    return {
        "manifestation": {
            "pid": pid,
            "ownerWork": {"workId": work_id},
            "titles": {"full": ["Some Title"]},
            "creators": [{"display": "A. Author"}],
            "cover": {"thumbnail": "https://example.invalid/cover.jpg"},
            "materialTypes": [{"materialTypeSpecific": {"display": "bog"}}],
        }
    }


def _ereolen_product(isbn="9788740003826"):
    """Build an eReolen pubhub product payload."""
    return {
        "title": "Kvinden i buret",
        "thumbnailUri": "https://example.invalid/thumb.jpg",
        "format": "epub",
        "externalProductId": {"idType": 15, "id": isbn},
        "internalProductId": "d69c230b-d037-4de7-afad-8ee701111673",
        "contributors": [{"firstName": "Jussi", "lastName": "Adler-Olsen"}],
    }


def test_physical_material_url_from_work_id():
    mat = libraryMaterial(_manifestation(work_id="work-of:870970-basis:51069609"))
    assert mat.title == "Some Title"
    assert mat.creators == "A. Author"
    assert mat.type == "bog"
    assert mat.url == "https://bibliotek.dk/work/work-of:870970-basis:51069609"


def test_physical_material_url_missing_owner_work():
    data = _manifestation()
    del data["manifestation"]["ownerWork"]
    mat = libraryMaterial(data)
    assert mat.url is None


def test_ereolen_material_url_from_isbn():
    mat = libraryMaterial(_ereolen_product(isbn="9788740003826"))
    assert mat.title == "Kvinden i buret"
    assert mat.creators == "JussiAdler-Olsen"
    assert mat.type == "epub"
    assert mat.url == "https://bibliotek.dk/?q=9788740003826"


def test_ereolen_material_url_missing_isbn():
    data = _ereolen_product()
    del data["externalProductId"]
    mat = libraryMaterial(data)
    assert mat.url is None


def test_material_parse_failure_is_swallowed():
    # Neither branch matches -> no exception, fields stay None.
    mat = libraryMaterial({"unexpected": True})
    assert mat.title is None
    assert mat.url is None


def test_debt_fee_type_and_amount():
    debt = libraryDebt(_manifestation())
    debt.feeType = "overdue"
    debt.feeAmount = "25.50"
    assert debt.feeType == "overdue"
    assert debt.feeAmount == "25.50"


def test_sort_lists_orders_loans_by_expire_then_title():
    lib = Library("1234", "0000", "https://example.invalid", "710100")
    later = libraryLoan(_manifestation())
    later.title = "B"
    later.expireDate = dt.datetime(2026, 2, 1)
    sooner = libraryLoan(_manifestation())
    sooner.title = "A"
    sooner.expireDate = dt.datetime(2026, 1, 1)
    # A loan with no expireDate must sort last.
    undated = libraryLoan(_manifestation())
    undated.title = "C"
    undated.expireDate = None

    lib.user.loans = [later, undated, sooner]
    lib.user.reservations = []
    lib.user.reservationsReady = []
    lib.sortLists()

    assert [loan.title for loan in lib.user.loans] == ["A", "B", "C"]


def test_sort_lists_orders_reservations_by_queue_number():
    lib = Library("1234", "0000", "https://example.invalid", "710100")
    q3 = libraryReservation(_manifestation())
    q3.title = "third"
    q3.queueNumber = 3
    q3.createdDate = dt.datetime(2026, 1, 1)
    q1 = libraryReservation(_manifestation())
    q1.title = "first"
    q1.queueNumber = 1
    q1.createdDate = dt.datetime(2026, 1, 2)

    lib.user.loans = []
    lib.user.reservations = [q3, q1]
    lib.user.reservationsReady = []
    lib.sortLists()

    assert [r.title for r in lib.user.reservations] == ["first", "third"]


def test_sort_lists_orders_ready_reservations_by_pickup_date():
    lib = Library("1234", "0000", "https://example.invalid", "710100")
    later = libraryReservationReady(_manifestation())
    later.title = "later"
    later.pickupDate = dt.datetime(2026, 3, 1)
    sooner = libraryReservationReady(_manifestation())
    sooner.title = "sooner"
    sooner.pickupDate = dt.datetime(2026, 2, 1)

    lib.user.loans = []
    lib.user.reservations = []
    lib.user.reservationsReady = [later, sooner]
    lib.sortLists()

    assert [r.title for r in lib.user.reservationsReady] == ["sooner", "later"]


def test_branch_name_splits_id_and_uses_cache():
    lib = Library("1234", "0000", "https://example.invalid", "710100")
    lib.branches = {"123": "Some Branch"}
    # The id is split on '-' and the last part is looked up in the cache.
    assert lib._branchName("a-b-123") == "Some Branch"
    # Unknown id falls back to the id itself.
    assert lib._branchName("a-b-999") == "999"


def test_use_ereolen_defaults_true_and_is_settable():
    lib = Library("1234", "0000", "https://example.invalid", "710100")
    assert lib.use_eReolen is True
    lib2 = Library(
        "1234", "0000", "https://example.invalid", "710100", use_eReolen=False
    )
    assert lib2.use_eReolen is False


def test_token_expiry_is_none_before_first_login():
    # Regression: user_token compared an attribute that was never set.
    lib = Library("1234", "0000", "https://example.invalid", "710100")
    assert lib._user_token_exp is None
