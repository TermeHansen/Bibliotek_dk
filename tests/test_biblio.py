"""Synthetic contract tests for the new digital provider; no patron data."""

from __future__ import annotations

import datetime as dt
from unittest.mock import Mock

import pytest

from custom_components.bibliotek_dk.biblio import BiblioClient, BiblioError
from custom_components.bibliotek_dk.library_api import Library


BASE = "https://adapter.invalid"


def response(data=None, status=200):
    return Mock(status_code=status, json=Mock(return_value=data))


def loan(id="loan-1"):
    return {
        "id": id,
        "material_id": "9780000000001",
        "material_type": "ebook",
        "title": "A digital book",
        "author": "A. Author",
        "start": "2026-11-17T10:00:00+01:00",
        "end": "2026-12-17T10:00:00Z",
    }


def library():
    lib = Library("test-user", "test-pin", "https://library.invalid", "754000")
    lib._transport._user_token = "synthetic-token"  # noqa: S105 — test fixture
    lib._transport._user_token_exp = dt.datetime.now() + dt.timedelta(hours=1)
    lib._get = Mock()
    lib._biblio = BiblioClient(lib, BASE)
    return lib


def test_loans_follow_cursor_and_keep_digital_loans_out_of_renew_service():
    lib = library()
    lib._get.side_effect = [
        response({"loans": [loan()], "pagination": {"cursor": "next-page"}}),
        response({"loans": [loan("loan-2")], "pagination": {}}),
    ]
    loans = lib._biblio.loans()
    assert len(loans) == 2
    assert loans[0].loanDate == dt.datetime(2026, 11, 17, 9)
    assert loans[0].expireDate == dt.datetime(2026, 12, 17, 10)
    assert loans[0].title == "A digital book"
    assert loans[0].creators == "A. Author"
    assert loans[0].renewId is None
    assert loans[0].renewAble is False
    call = lib._get.call_args
    assert call.args[0] == f"{BASE}/v1/loans"
    assert call.kwargs["params"]["cursor"] == "next-page"
    assert call.kwargs["headers"]["Authorization"] == "Bearer synthetic-token"


def test_failed_later_page_keeps_all_previous_digital_and_fresh_physical_data():
    lib = library()
    old = Mock(title="Last successful digital loan")
    lib._loans["ereolen"] = [old]
    lib._get.side_effect = [
        response([]),  # Physical loans successfully refreshed to empty.
        response({"loans": [loan()], "pagination": {"cursor": "next"}}),
        response(status=503),
    ]
    lib.fetchLoans()
    assert lib.user.loans == [old]


def test_repeated_cursor_does_not_loop_forever():
    lib = library()
    lib._get.return_value = response({"loans": [], "pagination": {"cursor": "same"}})
    with pytest.raises(BiblioError, match="pagination"):
        lib._biblio.loans()
    assert lib._get.call_count == 2


@pytest.mark.parametrize("pagination", [None, [], "invalid"])
def test_malformed_pagination_keeps_previous_complete_source(pagination):
    lib = library()
    old = Mock()
    lib._loans["ereolen"] = [old]
    lib._get.side_effect = [
        response([]),
        response({"loans": [loan()], "pagination": pagination}),
    ]
    lib.fetchLoans()
    assert lib.user.loans == [old]


@pytest.mark.parametrize("status", [401, 403, 429, 503])
def test_failed_adapter_request_keeps_previous_reservations(status):
    lib = library()
    waiting, ready = Mock(), Mock()
    lib._reservations["ereolen"] = [waiting]
    lib._reservations_ready["ereolen"] = [ready]
    lib._get.side_effect = [response([]), response(status=status)]
    lib.fetchReservations()
    assert lib.user.reservations == [waiting]
    assert lib.user.reservationsReady == [ready]


def test_reservations_map_available_offers_and_cache_metadata():
    lib = library()
    item = {
        "id": "reservation-1",
        "material_id": "9780000000001",
        "material_type": "audiobook",
        "timestamp": "2026-11-17T09:00:00Z",
        "loan_date": "2026-11-25T09:00:00Z",
        "queue_position": 1,
    }
    offered = {
        **item,
        "id": "reservation-2",
        "offer_id": "offer-1",
        "offer_expires_at": "2026-11-20T09:00:00Z",
    }
    lib._get.side_effect = [
        response({"reservations": [item, offered], "pagination": {}}),
        response(
            {
                "materials": [
                    {
                        "isbn": item["material_id"],
                        "title": "An audiobook",
                        "author": ["Author"],
                    }
                ]
            }
        ),
    ]
    waiting, ready = lib._biblio.reservations()
    assert len(waiting) == len(ready) == 1
    assert waiting[0].title == ready[0].title == "An audiobook"
    assert waiting[0].queueNumber == 1
    assert ready[0].pickupDate == dt.datetime(2026, 11, 20, 9)
    assert ready[0].pickupLibrary == "biblo.dk"
    assert lib._get.call_count == 2


def test_metadata_outage_does_not_hide_reservation():
    lib = library()
    lib._get.side_effect = [
        response(
            {
                "reservations": [
                    {**loan(), "timestamp": "2026-11-17", "loan_date": "2026-12-01"}
                ],
                "pagination": {},
            }
        ),
        response(status=503),
    ]
    waiting, ready = lib._biblio.reservations()
    assert len(waiting) == 1
    assert not ready


@pytest.mark.parametrize("split", [True, False])
def test_shared_quotas_are_not_reported_as_two_independent_allowances(split):
    lib = library()
    quota = {
        "split_on_format": split,
        "combined_max_concurrent_user_loans": 5,
        "max_concurrent_user_loans": {"ebook": 3, "audiobook": 2},
    }
    lib._get.side_effect = [
        response([]),
        response({"loans": [loan()], "pagination": {}}),
        response({"loan_quotas": [quota]}),
    ]
    lib.fetchLoans()
    assert lib.user.eBooks == 1
    assert lib.user.audioBooks == 0
    assert lib.user.eBooksQuota == (3 if split else None)
    assert lib.user.audioBooksQuota == (2 if split else None)
    assert lib.user.digitalLoansQuota == [quota]


@pytest.mark.parametrize("enabled", ["0", "1"])
def test_website_flag_selects_provider_without_new_account_settings(enabled):
    lib = library()
    lib._biblio = None
    lib._get.return_value = response({}, status=200)
    lib._get.return_value.text = (
        f'<div data-biblio-base-url="{BASE}" '
        f'data-use-biblio-adapter-config="{enabled}"></div>'
    )
    lib.fetchUserInfo = Mock(return_value=True)
    lib._branchName = Mock()
    lib.fetchLoans = Mock()
    lib.fetchReservations = Mock()
    lib.fetchDebts = Mock()
    lib.update()
    assert (lib._biblio is not None) == (enabled == "1")


def test_disabled_digital_option_clears_new_provider_state_without_api_call():
    lib = library()
    lib.use_eReolen = False
    lib.user.otherDigitalLoans = 2
    lib._get.return_value = response([])
    lib._loans["ereolen"] = [Mock()]
    lib.fetchLoans()
    assert lib.user.loans == []
    assert lib.user.otherDigitalLoans == 0
    assert lib._get.call_count == 1  # Only the physical API.


@pytest.mark.parametrize("broken", [None, {}, {**loan(), "end": "not-a-date"}])
def test_malformed_loan_does_not_discard_valid_loans(broken, caplog):
    lib = library()
    lib._get.return_value = response(
        {"loans": [loan("before"), broken, loan("after")], "pagination": {}}
    )
    assert [item.id for item in lib._biblio.loans()] == ["before", "after"]
    assert "Skipping malformed Biblo loan" in caplog.text


@pytest.mark.parametrize("broken", [None, {}, {**loan(), "timestamp": "invalid"}])
def test_malformed_reservation_does_not_discard_waiting_or_ready(broken, caplog):
    lib = library()
    waiting = {**loan(), "timestamp": "2026-11-17", "loan_date": "2026-12-01"}
    ready = {
        **waiting,
        "id": "ready",
        "offer_id": "offer",
        "offer_expires_at": "2026-12-01",
    }
    lib._get.side_effect = [
        response({"reservations": [waiting, broken, ready], "pagination": {}}),
        response({"materials": []}),
    ]
    pending, offered = lib._biblio.reservations()
    assert [item.id for item in pending] == ["loan-1"]
    assert [item.id for item in offered] == ["ready"]
    assert "Skipping malformed Biblo reservation" in caplog.text


@pytest.mark.parametrize(
    "materials", [None, {}, [None], [{"isbn": "9780000000001", "author": [None]}]]
)
def test_malformed_metadata_is_retried_and_recovery_is_cached(materials):
    lib = library()
    item = {**loan(), "timestamp": "2026-11-17", "loan_date": "2026-12-01"}
    page = response({"reservations": [item], "pagination": {}})
    lib._get.side_effect = [
        page,
        response({"materials": materials}),
        page,
        response(
            {
                "materials": [
                    {
                        "isbn": item["material_id"],
                        "title": "Recovered",
                        "author": ["Author"],
                    }
                ]
            }
        ),
        page,
    ]
    assert len(lib._biblio.reservations()[0]) == 1
    assert not lib._biblio._metadata
    assert lib._biblio.reservations()[0][0].title == "Recovered"
    assert lib._biblio.reservations()[0][0].creators == "Author"
    assert lib._get.call_count == 5


def test_optional_queue_positions_sort_and_reach_sensor():
    from custom_components.bibliotek_dk.sensor import ReservationSensor

    lib = library()
    base = {**loan(), "timestamp": "2026-11-17", "loan_date": "2026-12-01"}
    items = [
        {**base, "id": "behind", "queue_position": 9},
        {**base, "id": "front", "queue_position": 1},
        {**base, "id": "absent"},
    ]
    lib._get.side_effect = [
        response({"reservations": items, "pagination": {}}),
        response({"materials": []}),
    ]
    lib.user.reservations, lib.user.reservationsReady = lib._biblio.reservations()
    lib.sortLists()
    assert [r.id for r in lib.user.reservations] == ["front", "behind", "absent"]
    sensor = ReservationSensor(lib.user, Mock(last_update_success=True))
    assert [
        r["queue_number"] for r in sensor.extra_state_attributes["reservations"]
    ] == [1, 9, None]


def test_other_material_types_are_preserved_counted_and_exposed():
    from custom_components.bibliotek_dk.sensor import LibrarySensor

    lib = library()
    lib.libraryName = "Test Library"
    items = [
        {**loan(str(i)), "material_type": kind}
        for i, kind in enumerate(
            ["ebook", "audiobook", "podcast", "paper_book", "future_type"]
        )
    ]
    lib._get.side_effect = [
        response([]),
        response({"loans": items, "pagination": {}}),
        response({"loan_quotas": []}),
    ]
    lib.fetchLoans()
    sensor = LibrarySensor(lib, Mock(last_update_success=True))
    attrs = sensor.extra_state_attributes
    assert (attrs["ebooks"], attrs["audiobooks"], attrs["other_digital_loans"]) == (
        1,
        1,
        3,
    )
    assert sum(
        attrs[k] for k in ("ebooks", "audiobooks", "other_digital_loans")
    ) == len(lib._loans["ereolen"])
    assert [item.type for item in lib._loans["ereolen"]] == [
        "epub",
        "audiobook",
        "podcast",
        "paper_book",
        "future_type",
    ]


def test_failed_digital_refresh_keeps_other_material_count():
    lib = library()
    lib.user.otherDigitalLoans = 3
    lib._get.return_value = response(status=503)
    lib._fetchBiblioLoans()
    assert lib.user.otherDigitalLoans == 3


def test_active_podcasts_and_quota_usage_are_distinct_and_retained_on_quota_outage():
    from custom_components.bibliotek_dk.sensor import LibrarySensor
    from tests.test_quotas import split_quota

    lib = library()
    lib.libraryName = "Test Library"
    page = response(
        {
            "loans": [{**loan(str(i)), "material_type": "podcast"} for i in range(5)],
            "pagination": {},
        }
    )
    lib._get.side_effect = [
        response([]),
        page,
        response({"loan_quotas": [split_quota()]}),
        response([]),
        page,
        response(status=503),
    ]
    lib.fetchLoans()
    attrs = LibrarySensor(lib, Mock(last_update_success=True)).extra_state_attributes
    assert attrs["podcasts"] == 5
    assert attrs["podcasts_quota_used"] == 0
    assert attrs["podcasts_quota"] == 3
    assert attrs["other_digital_loans"] == 5
    lib.fetchLoans()
    assert lib.user.digitalQuotaAttributes["podcasts_quota_used"] == 0
    lib.use_eReolen = False
    lib._get.side_effect = [response([])]
    lib.fetchLoans()
    assert lib.user.podcasts == 0
    assert all(v is None for v in lib.user.digitalQuotaAttributes.values())
