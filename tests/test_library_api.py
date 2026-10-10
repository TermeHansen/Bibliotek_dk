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


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


class _FakeSession:
    """Session stub returning queued responses per HTTP verb."""

    def __init__(self, post=None, get=None):
        self._post = post
        self._get = get

    def post(self, url, **kwargs):
        return self._post

    def get(self, url, **kwargs):
        return self._get


def _ready_library(session):
    """A Library with a valid token and a stubbed session (no login calls)."""
    lib = Library("1234", "0000", "https://example.invalid", "710100")
    lib.session = session
    lib._urls = {"data-fbi-global-base-url": "https://details.invalid/graphql"}
    lib._user_token = "x" * 8
    lib._user_token_exp = dt.datetime.now() + dt.timedelta(hours=1)
    return lib


def test_session_is_configured_with_timeout_and_retries():
    from custom_components.bibliotek_dk import library_api

    lib = Library("1234", "0000", "https://example.invalid", "710100")
    adapter = lib.session.get_adapter("https://example.invalid")
    assert adapter.max_retries.total == 3
    assert 500 in adapter.max_retries.status_forcelist
    # The helpers apply a default timeout when the caller does not pass one.
    assert library_api.REQUEST_TIMEOUT == (10, 30)


def test_get_details_returns_empty_dict_on_non_200():
    # Regression: a failed details fetch used to raise KeyError on the cache.
    session = _FakeSession(post=_FakeResponse(status_code=500))
    lib = _ready_library(session)
    assert lib._getDetails("faust-1") == {}
    # The failure must not be cached as a hit.
    assert "faust-1" not in lib._details


def test_get_details_caches_successful_lookup():
    payload = {"data": {"manifestation": {"pid": "p"}}}
    session = _FakeSession(post=_FakeResponse(payload=payload))
    lib = _ready_library(session)
    assert lib._getDetails("faust-1") == payload["data"]
    assert lib._details["faust-1"] == payload["data"]


def test_fetch_debts_keeps_fee_without_materials():
    # A fee with an empty materials list must still be counted, not crash.
    lib = _ready_library(_FakeSession())
    fees = [{"creationDate": "2026-01-01", "amount": "25.00", "type": "overdue", "materials": []}]
    lib.session = _FakeSession(get=_FakeResponse(payload=fees))
    lib.get_depts = True

    lib.fetchDebts()

    assert len(lib.user.debts) == 1
    assert lib.user.debts[0].feeAmount == "25.00"
    assert lib.user.debts[0].feeType == "overdue"
    assert lib.user.debtsAmount == 25.0


def test_update_refreshes_profile_at_most_daily():
    lib = _ready_library(_FakeSession())
    lib._branchName = lambda _id: "Branch"
    calls = {"n": 0}

    def fake_fetch():
        calls["n"] += 1
        lib.user.name = "Test User"

    lib.fetchUserInfo = fake_fetch
    lib.get_loans = lib.get_reservations = lib.get_depts = False

    lib.update()  # first poll -> fetch
    lib.update()  # same day -> no fetch
    assert calls["n"] == 1

    # Force the timestamp into the past and poll again.
    lib._user_info_fetched = dt.datetime.now() - dt.timedelta(days=2)
    lib.update()
    assert calls["n"] == 2


def test_all_log_records_format_without_error(caplog):
    # Regression: update()'s debug message had 3 placeholders but 4 args, which
    # made the logging handler raise "not all arguments converted".
    import logging

    lib = _ready_library(_FakeSession())
    lib._branchName = lambda _id: "Branch"
    lib.fetchUserInfo = lambda: setattr(lib.user, "name", "Test User")
    lib.fetchLoans = lib.fetchReservations = lib.fetchDebts = lambda: None

    with caplog.at_level(logging.DEBUG, logger="custom_components.bibliotek_dk"):
        lib.update()

    # Formatting every record is what the handler does; a mismatch raises here.
    for record in caplog.records:
        record.getMessage()
    assert any("Updating" in r.getMessage() for r in caplog.records)
