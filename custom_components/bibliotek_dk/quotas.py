"""Map authoritative Biblio quota usage without counting active loan records."""

from __future__ import annotations

from math import isfinite

FORMATS = {"ebooks": "ebook", "audiobooks": "audiobook", "podcasts": "podcast"}


def _number(value):
    return (
        value
        if type(value) in (int, float) and isfinite(value) and value >= 0
        else None
    )


def _field(quota, field, kind):
    values = quota.get(field)
    return values.get(kind) if isinstance(values, dict) else None


def quota_attributes(quotas):
    """Only flatten a single organization; ambiguous/missing values are unknown.

    The API calls period usage ``current_monthly_loans``, even for configured
    periods other than a month. Only expose monthly aliases for a known month.
    """
    result = {}
    for prefix in (*FORMATS, "shared"):
        for suffix in (
            "quota",
            "quota_used",
            "monthly_quota",
            "monthly_quota_used",
            "period_quota",
            "period_quota_used",
            "quota_period",
        ):
            result[f"{prefix}_{suffix}"] = None
    if (
        not isinstance(quotas, list)
        or len(quotas) != 1
        or not isinstance(quotas[0], dict)
    ):
        return result
    quota = quotas[0]
    split = quota.get("split_on_format")
    if type(split) is not bool:
        return result
    formats = FORMATS.items() if split else [("shared", None)]
    for prefix, kind in formats:

        def read(field, kind=kind):
            return (
                _field(quota, field, kind) if split else quota.get(f"combined_{field}")
            )

        result[f"{prefix}_quota"] = _number(read("max_concurrent_user_loans"))
        result[f"{prefix}_quota_used"] = _number(read("current_concurrent_loans"))
        maximum = _number(read("max_user_loans"))
        used = _number(read("current_monthly_loans"))
        result[f"{prefix}_period_quota"] = maximum
        result[f"{prefix}_period_quota_used"] = used
        period = read("max_user_loans_period")
        if isinstance(period, dict):
            days, months = _number(period.get("days")), _number(period.get("months"))
            if days is not None and months is not None:
                result[f"{prefix}_quota_period"] = {"days": days, "months": months}
                if days == 0 and months == 1:
                    result[f"{prefix}_monthly_quota"] = maximum
                    result[f"{prefix}_monthly_quota_used"] = used
    return result
