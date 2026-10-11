"""Unit tests for the Library facade and its per-resource fetchers.

Model parsing lives in ``test_models.py`` and transport/token handling in
``test_transport.py``; these tests exercise the coordinator-facing surface.
"""

from __future__ import annotations

import datetime as dt

from custom_components.bibliotek_dk import library_api
from custom_components.bibliotek_dk.library_api import (
    Library,
    libraryLoan,
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


def test_host_and_session_are_exposed_from_transport():
    lib = Library("1234", "0000", "https://example.invalid", "710100")
    assert lib.host == "https://example.invalid"
    # The session is the transport's session (closed on unload).
    assert lib.session is lib._transport.session


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

    def close(self):
        return None


def _ready_library(session):
    """A Library with a valid token and a stubbed session (no login calls)."""
    lib = Library("1234", "0000", "https://example.invalid", "710100")
    lib.session = session
    lib._urls = {"data-fbi-global-base-url": "https://details.invalid/graphql"}
    lib._transport._user_token = "x" * 8
    lib._transport._user_token_exp = dt.datetime.now() + dt.timedelta(hours=1)
    return lib


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
    fees = [
        {
            "creationDate": "2026-01-01",
            "amount": "25.00",
            "type": "overdue",
            "materials": [],
        }
    ]
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


def test_public_constants_are_reexported():
    # Callers/tests import these from library_api; keep them stable.
    assert library_api.REQUEST_TIMEOUT == (10, 30)
    assert library_api.DETAILS_CACHE_MAX == 500
