"""Synthetic quota fixtures: actual usage is independent of active loans."""

import pytest

from custom_components.bibliotek_dk.quotas import quota_attributes


def split_quota():
    return {
        "split_on_format": True,
        "max_concurrent_user_loans": {"ebook": 120, "audiobook": 120, "podcast": 3},
        "current_concurrent_loans": {"ebook": 2, "audiobook": 3, "podcast": 0},
        "max_user_loans": {"ebook": 120, "audiobook": 120, "podcast": 10},
        "current_monthly_loans": {"ebook": 4, "audiobook": 5, "podcast": 0},
        "max_user_loans_period": {
            "ebook": {"days": 0, "months": 1},
            "audiobook": {"days": 0, "months": 1},
        },
    }


def test_split_quota_usage_zero_and_missing_period():
    attrs = quota_attributes([split_quota()])
    assert attrs["podcasts_quota_used"] == 0
    assert attrs["podcasts_quota"] == 3
    assert attrs["ebooks_monthly_quota_used"] == 4
    assert attrs["audiobooks_monthly_quota"] == 120
    assert attrs["podcasts_period_quota"] == 10
    assert attrs["podcasts_period_quota_used"] == 0
    assert attrs["podcasts_monthly_quota"] is None
    assert attrs["podcasts_monthly_quota_used"] is None
    assert attrs["shared_quota_used"] is None


def test_nonmonthly_period_is_not_mislabeled_as_monthly():
    quota = split_quota()
    quota["max_user_loans_period"]["podcast"] = {"months": 3, "days": 0}
    attrs = quota_attributes([quota])
    assert attrs["podcasts_period_quota"] == 10
    assert attrs["podcasts_quota_period"] == {"months": 3, "days": 0}
    assert attrs["podcasts_monthly_quota"] is None


def test_combined_quota_does_not_invent_per_format_allowances():
    attrs = quota_attributes(
        [
            {
                "split_on_format": False,
                "combined_max_concurrent_user_loans": 5,
                "combined_current_concurrent_loans": 2,
                "combined_current_monthly_loans": 3,
                "combined_max_user_loans": 10,
                "combined_max_user_loans_period": {"days": 0, "months": 1},
            }
        ]
    )
    assert attrs["shared_quota_used"] == 2
    assert attrs["shared_quota"] == 5
    assert attrs["shared_monthly_quota_used"] == 3
    assert attrs["shared_monthly_quota"] == 10
    assert attrs["ebooks_quota_used"] is None
    assert attrs["podcasts_quota"] is None


@pytest.mark.parametrize(
    "quotas", [None, [], [None], [{}, {}], [{"split_on_format": "true"}]]
)
def test_unknown_or_multiple_organizations_are_not_summed(quotas):
    assert all(value is None for value in quota_attributes(quotas).values())


@pytest.mark.parametrize("value", [None, -1, True, "3", float("nan"), float("inf")])
def test_invalid_usage_remains_unknown(value):
    quota = split_quota()
    quota["current_concurrent_loans"]["podcast"] = value
    assert quota_attributes([quota])["podcasts_quota_used"] is None
