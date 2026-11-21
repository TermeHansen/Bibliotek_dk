"""Unit tests for the Library facade and its per-resource fetchers.

Model parsing lives in ``test_models.py`` and transport/token handling in
``test_transport.py``; these tests exercise the coordinator-facing surface.
"""

from __future__ import annotations

import datetime as dt

import pytest

from custom_components.bibliotek_dk import library_api
from custom_components.bibliotek_dk.library_api import (
    Library,
    libraryDebt,
    libraryLoan,
    libraryReservation,
    libraryReservationReady,
)


def test_sort_lists_orders_loans_by_expire_then_title():
    lib = Library("1234", "0000", "https://example.invalid", "710100")
    later = libraryLoan(_record())
    later.title = "B"
    later.expireDate = dt.datetime(2026, 2, 1)
    sooner = libraryLoan(_record())
    sooner.title = "A"
    sooner.expireDate = dt.datetime(2026, 1, 1)
    # A loan with no expireDate must sort last.
    undated = libraryLoan(_record())
    undated.title = "C"
    undated.expireDate = None

    lib.user.loans = [later, undated, sooner]
    lib.user.reservations = []
    lib.user.reservationsReady = []
    lib.sortLists()

    assert [loan.title for loan in lib.user.loans] == ["A", "B", "C"]


def test_sort_lists_orders_reservations_by_queue_number():
    lib = Library("1234", "0000", "https://example.invalid", "710100")
    q3 = libraryReservation(_record())
    q3.title = "third"
    q3.queueNumber = 3
    q3.createdDate = dt.datetime(2026, 1, 1)
    q1 = libraryReservation(_record())
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
    later = libraryReservationReady(_record())
    later.title = "later"
    later.pickupDate = dt.datetime(2026, 3, 1)
    sooner = libraryReservationReady(_record())
    sooner.title = "sooner"
    sooner.pickupDate = dt.datetime(2026, 2, 1)

    lib.user.loans = []
    lib.user.reservations = []
    lib.user.reservationsReady = [later, sooner]
    lib.sortLists()

    assert [r.title for r in lib.user.reservationsReady] == ["sooner", "later"]


@pytest.mark.parametrize(
    ("list_name", "model", "date_attr"),
    [("loansOverdue", libraryLoan, "expireDate"), ("debts", libraryDebt, "feeDate")],
)
def test_sort_overdue_and_debts_by_date_then_title_with_missing_values(
    list_name, model, date_attr
):
    lib = Library("1234", "0000", "https://example.invalid", "710100")
    # Input order differs from date/title order; missing dates must sort last.
    specs = [
        ("Later", dt.datetime(2026, 2, 1)),
        ("Undated B", None),
        ("B", dt.datetime(2026, 1, 1)),
        (None, dt.datetime(2026, 1, 1)),
        ("A", dt.datetime(2026, 1, 1)),
        ("Undated A", None),
    ]
    items = []
    for title, date in specs:
        item = model(_record())
        item.title = title
        setattr(item, date_attr, date)
        items.append(item)
    setattr(lib.user, list_name, items)

    lib.sortLists()

    assert [item.title for item in getattr(lib.user, list_name)] == [
        None,
        "A",
        "B",
        "Later",
        "Undated A",
        "Undated B",
    ]
    assert len(getattr(lib.user, list_name)) == len(specs)


def test_branch_name_splits_id_and_uses_cache():
    lib = Library("1234", "0000", "https://example.invalid", "710100")
    lib.branches = {"123": "Some Branch"}
    # The id is split on '-' and the last part is looked up in the cache.
    assert lib._branchName("a-b-123") == "Some Branch"
    # A cache miss fetches the branch list; stub the session so no network is hit.
    payload = {"data": {"branches": {"result": [{"branchId": "999", "name": "New"}]}}}
    lib.session = _FakeSession(post=_FakeResponse(payload=payload))
    assert lib._branchName("a-b-999") == "New"
    assert lib.branches["999"] == "New"


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
        self.last_kwargs = None

    def post(self, url, **kwargs):
        self.last_kwargs = kwargs
        return self._post

    def get(self, url, **kwargs):
        self.last_kwargs = kwargs
        return self._get

    def close(self):
        return None


def _ready_library(session):
    """A Library with a valid token and a stubbed session (no login calls)."""
    lib = Library("1234", "0000", "https://example.invalid", "710100")
    lib.session = session
    # Pre-seed the discovered config so update() does not hit the network, and
    # pin the adapter flag off so no BiblioClient is built.
    lib._urls = {"data-use-biblio-adapter-config": "0"}
    lib._transport._user_token = "x" * 8
    lib._transport._user_token_exp = dt.datetime.now() + dt.timedelta(hours=1)
    return lib


def _record(**overrides):
    """A record payload as the Biblo API returns it (flat fields)."""
    payload = {
        "recordId": "870970-basis:123",
        "workId": "work-of:870970-basis:123",
        "title": "Some Title",
        "creators": ["A. Author"],
        "coverUrl": "https://example.invalid/cover.jpg",
        "materialType": {"display": "bog", "category": "BOOKS"},
    }
    payload.update(overrides)
    return payload


def test_get_details_returns_empty_dict_on_non_200():
    # Regression: a failed details fetch used to raise KeyError on the cache.
    session = _FakeSession(get=_FakeResponse(status_code=500))
    lib = _ready_library(session)
    assert lib._getDetails("faust-1") == {}
    # The failure must not be cached as a hit.
    assert "faust-1" not in lib._details


def test_get_details_caches_successful_lookup():
    payload = {"data": _record()}
    session = _FakeSession(get=_FakeResponse(payload=payload))
    lib = _ready_library(session)
    record = lib._getDetails("faust-1")
    assert record["title"] == "Some Title"
    assert lib._details["faust-1"] == record


def test_get_details_maps_record_onto_material():
    session = _FakeSession(get=_FakeResponse(payload={"data": _record()}))
    lib = _ready_library(session)
    loan = libraryLoan(lib._getDetails("faust-1"))
    assert loan.title == "Some Title"
    assert loan.creators == "A. Author"
    assert loan.coverUrl == "https://example.invalid/cover.jpg"
    assert loan.type == "bog"
    assert loan.url == "https://bibliotek.dk/work/work-of:870970-basis:123"


def test_get_details_tolerates_missing_optional_fields():
    # A sparse record must not raise; the loan keeps its dates and loses only
    # the fields that are absent.
    sparse = {"recordId": "r", "title": "Only A Title"}
    session = _FakeSession(get=_FakeResponse(payload={"data": sparse}))
    lib = _ready_library(session)
    loan = libraryLoan(lib._getDetails("faust-1"))
    assert loan.title == "Only A Title"
    assert loan.creators is None
    assert loan.coverUrl is None


def test_get_details_unexpected_payload_is_not_cached():
    # A 200 without the `data` envelope is a failed lookup, not a hit.
    session = _FakeSession(get=_FakeResponse(payload={"unexpected": True}))
    lib = _ready_library(session)
    assert lib._getDetails("faust-1") == {}
    assert "faust-1" not in lib._details


def test_get_details_null_data_is_a_failed_lookup():
    # Regression: a 200 carrying {"data": null} used to be cached as a hit, so
    # the caller got None instead of {} and every later lookup for that id
    # returned it too — blanking the item's details for the process lifetime.
    session = _FakeSession(get=_FakeResponse(payload={"data": None}))
    lib = _ready_library(session)

    assert lib._getDetails("faust-1") == {}
    assert "faust-1" not in lib._details
    # The caller keeps the loan, which simply has no material details.
    assert libraryLoan(lib._getDetails("faust-1")).title is None


def test_get_details_sends_client_hints_and_no_bearer_token():
    # The record endpoint is anonymous: sending a patron token would couple a
    # public lookup to the session and leak it to a third-party host.
    session = _FakeSession(get=_FakeResponse(payload={"data": _record()}))
    lib = _ready_library(session)
    lib._getDetails("faust-1")
    headers = session.last_kwargs["headers"]
    assert headers["x-app-type"] == "mybiblo"
    assert "x-api-key" in headers
    assert "Authorization" not in headers


def test_get_details_expands_a_bare_faust_to_a_record_id():
    # Regression: FBS reports a bare faust ("39134543") and the API answers 400
    # for it, which silently blanked every title and cover.
    seen = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        return _FakeResponse(payload={"data": _record()})

    session = _FakeSession(get=None)
    session.get = fake_get
    lib = _ready_library(session)

    lib._getDetails("39134543")

    assert seen["url"].endswith("/records/870970-basis:39134543")


def test_get_details_leaves_an_already_qualified_id_alone():
    seen = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        return _FakeResponse(payload={"data": _record()})

    session = _FakeSession(get=None)
    session.get = fake_get
    lib = _ready_library(session)

    lib._getDetails("710100-katalog:52616417")

    assert seen["url"].endswith("/records/710100-katalog:52616417")


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
    lib.get_debts = True

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
        return True

    lib.fetchUserInfo = fake_fetch
    lib.get_loans = lib.get_reservations = lib.get_debts = False

    lib.update()  # first poll -> fetch
    lib.update()  # same day -> no fetch
    assert calls["n"] == 1

    # Force the timestamp into the past and poll again.
    lib._user_info_fetched = dt.datetime.now() - dt.timedelta(days=2)
    lib.update()
    assert calls["n"] == 2


def test_update_retries_profile_after_failed_fetch():
    # Regression: a failed refresh advanced the timestamp via the stale name,
    # so a transient error (e.g. HTTP 429) blocked retries for a day.
    lib = _ready_library(_FakeSession())
    lib._branchName = lambda _id: "Branch"
    calls = {"n": 0}

    def failing_fetch():
        calls["n"] += 1
        return False

    lib.fetchUserInfo = failing_fetch
    lib.get_loans = lib.get_reservations = lib.get_debts = False

    lib.update()
    lib.update()
    assert calls["n"] == 2
    assert lib._user_info_fetched is None


def test_fetch_loans_keeps_loan_when_details_fail():
    # Regression: a failed details lookup dropped an outstanding loan entirely.
    loans = [
        {
            "loanDetails": {
                "recordId": "faust-1",
                "loanId": "loan-1",
                "loanDate": "2026-01-01",
                "dueDate": "2030-01-01",
                "materialItemNumber": "item-1",
            },
            "isRenewable": True,
        }
    ]
    session = _FakeSession(
        get=_FakeResponse(payload=loans), post=_FakeResponse(status_code=500)
    )
    lib = _ready_library(session)
    lib.use_eReolen = False

    lib.fetchLoans()

    assert len(lib.user.loans) == 1
    assert lib.user.loans[0].expireDate is not None
    assert lib.user.loans[0].renewAble is True


def test_fetch_reservations_keeps_reservation_when_details_fail():
    reservations = [
        {
            "transactionId": "t1",
            "recordId": "faust-1",
            "state": "reserved",
            "dateOfReservation": "2026-01-01",
            "pickupBranch": "branch-1",
            "expiryDate": "2030-02-01",
            "numberInQueue": 3,
        }
    ]
    session = _FakeSession(
        get=_FakeResponse(payload=reservations), post=_FakeResponse(status_code=500)
    )
    lib = _ready_library(session)
    lib.use_eReolen = False
    lib._branchName = lambda _id: "Branch"

    lib.fetchReservations()

    assert len(lib.user.reservations) == 1
    assert lib.user.reservations[0].queueNumber == 3


def test_sort_lists_handles_missing_titles():
    # Regression: a loan/reservation kept without details has title None; a
    # shared sort date must not mix None with str (TypeError aborts the refresh).
    lib = Library("1234", "0000", "https://example.invalid", "710100")
    due = dt.datetime(2026, 1, 1)
    titled = libraryLoan(_record())
    titled.title = "A"
    titled.expireDate = due
    untitled = libraryLoan(_record())
    untitled.title = None
    untitled.expireDate = due

    lib.user.loans = [titled, untitled]
    lib.user.reservations = []
    lib.user.reservationsReady = []
    lib.sortLists()

    # No TypeError; both kept, the None title treated as "" sorts first.
    assert [loan.title for loan in lib.user.loans] == [None, "A"]


def test_fetch_loans_keeps_previous_state_on_api_error():
    # Regression: with raise_on_status=False a 5xx reaches the warning branch;
    # the list must not be replaced with empty data.
    lib = _ready_library(_FakeSession(get=_FakeResponse(status_code=503)))
    lib.use_eReolen = False
    existing = libraryLoan(_record())
    existing.title = "Existing"
    lib._loans["physical"] = [existing]

    lib.fetchLoans()

    assert lib.user.loans == [existing]


def test_fetch_reservations_keeps_previous_state_on_api_error():
    lib = _ready_library(_FakeSession(get=_FakeResponse(status_code=503)))
    lib.use_eReolen = False
    existing = libraryReservation(_record())
    existing.title = "Existing"
    lib._reservations["physical"] = [existing]

    lib.fetchReservations()

    assert lib.user.reservations == [existing]


def test_fetch_loans_keeps_physical_when_ereolen_fails():
    # Regression: requiring both sources discarded good physical results when
    # eReolen was down.
    loans = [
        {
            "loanDetails": {
                "recordId": "faust-1",
                "loanId": "loan-1",
                "loanDate": "2026-01-01",
                "dueDate": "2030-01-01",
                "materialItemNumber": "item-1",
            },
            "isRenewable": True,
        }
    ]
    responses = [_FakeResponse(payload=loans), _FakeResponse(status_code=503)]

    def fake_get(url, **kwargs):
        # Dispatch on the URL rather than call order: the details lookup and
        # the eReolen call are both GETs, so a queue would be order-fragile.
        if "/records/" in url:
            return _FakeResponse(payload={"data": _record()})
        if "pubhub" in url:
            return responses.pop()
        return responses.pop(0)

    session = _FakeSession(post=_FakeResponse(status_code=500), get=None)
    session.get = fake_get
    lib = _ready_library(session)
    lib.use_eReolen = True

    lib.fetchLoans()

    # Physical loan kept even though the eReolen call failed.
    assert len(lib.user.loans) == 1


def test_fetch_loans_keeps_ereolen_when_physical_fails():
    ereolen = {
        "userData": {"totalEbookLoans": 1, "totalAudioLoans": 0},
        "libraryData": {
            "maxConcurrentEbookLoansPerBorrower": 3,
            "maxConcurrentAudiobookLoansPerBorrower": 3,
        },
        "loans": [
            {
                "libraryBook": {"identifier": "id-1"},
                "orderDateUtc": "2026-01-01",
                "loanExpireDateUtc": "2030-01-01",
            }
        ],
    }
    product = {
        "product": {
            "title": "E",
            "thumbnailUri": "u",
            "format": "epub",
            "contributors": [],
        }
    }
    responses = [
        _FakeResponse(status_code=503),  # physical loans
        _FakeResponse(payload=ereolen),  # eReolen loans
        _FakeResponse(payload=product),  # eReolen product
    ]

    def fake_get(url, **kwargs):
        return responses.pop(0)

    session = _FakeSession(post=_FakeResponse(status_code=500), get=None)
    session.get = fake_get
    lib = _ready_library(session)
    lib.use_eReolen = True

    lib.fetchLoans()

    # eReolen loan kept even though the physical call failed.
    assert len(lib.user.loans) == 1
    assert lib.user.loans[0].title == "E"


def test_fetch_debts_keeps_previous_state_on_api_error():
    lib = _ready_library(_FakeSession(get=_FakeResponse(status_code=503)))
    existing = libraryDebt(_record())
    existing.feeAmount = "10.00"
    lib.user.debts = [existing]
    lib.user.debtsAmount = 10.0

    lib.fetchDebts()

    assert lib.user.debts == [existing]
    assert lib.user.debtsAmount == 10.0


def test_all_log_records_format_without_error(caplog):
    # Regression: update()'s debug message had 3 placeholders but 4 args, which
    # made the logging handler raise "not all arguments converted".
    import logging

    lib = _ready_library(_FakeSession())
    lib._branchName = lambda _id: "Branch"

    def fake_fetch():
        lib.user.name = "Test User"
        return True

    lib.fetchUserInfo = fake_fetch
    lib.fetchLoans = lib.fetchReservations = lib.fetchDebts = lambda: None

    with caplog.at_level(logging.DEBUG, logger="custom_components.bibliotek_dk"):
        lib.update()

    # Formatting every record is what the handler does; a mismatch raises here.
    for record in caplog.records:
        record.getMessage()
    assert any("Updating" in r.getMessage() for r in caplog.records)


def test_current_renewable_ids_include_overdue_and_exclude_ineligible_items():
    # This endpoint only contains physical loans. Due date does not decide
    # eligibility; the library may also allow an overdue loan to be renewed.
    data = [
        {"isRenewable": True, "loanDetails": {"loanId": 1, "dueDate": "2000-01-01"}},
        {"isRenewable": False, "loanDetails": {"loanId": 2}},
        {"isRenewable": True, "loanDetails": {"loanId": 3}},
        {"isRenewable": True, "loanDetails": {"loanId": 1}},
    ]
    session = _FakeSession(get=_FakeResponse(payload=data))
    lib = _ready_library(session)
    lib._loans["ereolen"] = [libraryLoan({})]
    assert lib.getRenewableLoanIds() == [1, 3]


@pytest.mark.parametrize("payload", [None, {}, [None], [{"isRenewable": True}]])
def test_renew_all_rejects_malformed_eligibility_instead_of_using_cached_loans(payload):
    lib = _ready_library(_FakeSession(get=_FakeResponse(payload=payload)))
    with pytest.raises((ValueError, KeyError, TypeError)):
        lib.getRenewableLoanIds()


def test_renew_all_eligibility_http_failure_does_not_fall_back_to_cache():
    lib = _ready_library(_FakeSession(get=_FakeResponse(status_code=503)))
    lib._loans["physical"] = [libraryLoan(_record())]
    with pytest.raises(ValueError, match="503"):
        lib.getRenewableLoanIds()


def test_renew_loans_posts_ids_and_maps_status():
    response = [
        {"loanDetails": {"loanId": 1}, "renewalStatus": ["renewed"]},
        {"loanDetails": {"loanId": 2}, "renewalStatus": ["deniedReserved"]},
        {"loanDetails": {"loanId": 3}, "renewalStatus": []},
    ]
    session = _FakeSession(post=_FakeResponse(payload=response))
    lib = _ready_library(session)

    status = lib.renewLoans(["1", "2", "3"])

    # The endpoint takes a bare JSON array of ids, not a wrapper object.
    assert session.last_kwargs["json"] == [1, 2, 3]
    assert status == {1: "renewed", 2: "deniedReserved", 3: None}


def test_renew_loans_returns_empty_on_error():
    session = _FakeSession(post=_FakeResponse(status_code=400))
    lib = _ready_library(session)
    assert lib.renewLoans(["1"]) == {}


def test_renew_loans_reports_ids_missing_from_response():
    # Regression (plan 7.5): ids absent from the reply used to vanish from the
    # status dict; they must show up as None ("no answer").
    response = [{"loanDetails": {"loanId": 1}, "renewalStatus": ["renewed"]}]
    session = _FakeSession(post=_FakeResponse(payload=response))
    lib = _ready_library(session)

    status = lib.renewLoans(["1", "2"])

    assert status == {1: "renewed", 2: None}


def test_update_raises_auth_error_without_user_token():
    # Regression (plan 7.1): a login that yields no user token used to keep
    # update() reporting success, leaving sensors available with stale data.
    lib = _ready_library(_FakeSession())
    lib._transport._user_token = ""
    lib._transport.loggedIn = ""
    # Stub the transport's login (the token property calls it internally);
    # the property must not hand update() a token.
    lib._transport.login = lambda: None

    with pytest.raises(library_api.LibraryAuthError):
        lib.update()


def test_fetch_loans_skips_malformed_entry():
    # Regression (plan 7.2): one unexpected payload used to abort the whole
    # refresh and blank every sensor.
    loans = [
        {
            "loanDetails": {
                "recordId": "faust-1",
                "loanId": "loan-1",
                "loanDate": "2026-01-01",
                "dueDate": "2030-01-01",
                "materialItemNumber": "item-1",
            },
            "isRenewable": True,
        },
        # Missing loanId/dueDate: must be skipped, not fatal.
        {"loanDetails": {"recordId": "faust-2"}},
    ]
    session = _FakeSession(
        get=_FakeResponse(payload=loans), post=_FakeResponse(status_code=500)
    )
    lib = _ready_library(session)
    lib.use_eReolen = False

    lib.fetchLoans()

    assert len(lib.user.loans) == 1
    assert lib.user.loans[0].renewId == "loan-1"


def test_fetch_debts_skips_malformed_fee():
    fees = [
        {"creationDate": "2026-01-01", "amount": "25.00", "type": "overdue"},
        # Missing amount: must be skipped, not fatal.
        {"creationDate": "2026-01-02"},
    ]
    lib = _ready_library(_FakeSession(get=_FakeResponse(payload=fees)))

    lib.fetchDebts()

    assert len(lib.user.debts) == 1
    assert lib.user.debtsAmount == 25.0


def test_fetch_user_info_survives_unexpected_casts():
    # Regression (plan 7.6): int(None) raised TypeError outside the handler
    # and failed the whole profile refresh.
    patron = {
        "patron": {
            "name": "Test User",
            "address": {"street": "S", "postalCode": "1000", "city": "C"},
            "phoneNumber": "123",
            "receiveSms": None,
            "emailAddress": "mail@example.invalid",
            "receiveEmail": True,
            "preferredPickupBranch": "b",
        }
    }
    lib = _ready_library(_FakeSession(get=_FakeResponse(payload=patron)))
    lib._branchName = lambda _id: "Branch"

    assert lib.fetchUserInfo() is False


def test_public_constants_are_reexported():
    # Callers/tests import these from library_api; keep them stable.
    assert library_api.REQUEST_TIMEOUT == (10, 30)
    assert library_api.DETAILS_CACHE_MAX == 500
