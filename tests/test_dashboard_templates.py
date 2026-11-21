"""Render the shipped cards with synthetic HA state functions, not patron data."""

from pathlib import Path
from types import SimpleNamespace

from jinja2 import StrictUndefined
from jinja2.nativetypes import NativeEnvironment
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def cards():
    dashboard = yaml.safe_load((ROOT / "dashboard.yaml").read_text())
    return {
        card["card"]["title"]: card
        for card in dashboard["views"][0]["cards"]
        if card["type"] == "custom:auto-entities"
    }


def sensor(kind, items, *, state="1", entry="account-a"):
    return SimpleNamespace(
        entity_id=f"sensor.{kind}_{entry}",
        state=state,
        entry=entry,
        attributes={kind: items, "user": entry},
    )


def render(template, sensors):
    env = NativeEnvironment(undefined=StrictUndefined)
    by_id = {item.entity_id: item for item in sensors}
    env.globals.update(
        integration_entities=lambda domain: list(by_id),
        expand=lambda ids: [by_id[key] for key in ids],
        has_value=lambda key: by_id[key].state not in ("unknown", "unavailable"),
        config_entry_id=lambda key: by_id[key].entry,
        as_datetime=lambda value, default=None: value if value else default,
        time_since=lambda value: "2 days",
        time_until=lambda value: "3 days",
    )
    return env.from_string(template).render()


def loan():
    return {
        "title": "Synthetic title",
        "type": "bog",
        "creators": None,
        "expire_date": None,
        "renewable": False,
        "renew_id": None,
        "cover": None,
    }


@pytest.mark.parametrize("source", ["dashboard", "standalone"])
@pytest.mark.parametrize("overdue", [False, True])
def test_bulk_button_uses_current_or_overdue_loans_not_cached_renewability(
    source, overdue
):
    card = (
        cards()["Bibliotekslån"]
        if source == "dashboard"
        else yaml.safe_load((ROOT / "loans_card_renewal.yaml").read_text())
    )
    states = [
        sensor("loans", [] if overdue else [loan()]),
        sensor("loans_overdue", [loan()] if overdue else []),
    ]
    result = render(card["filter"]["template"], states)
    buttons = [row for row in result if row["type"] == "button"]
    assert len(buttons) == 1
    action = buttons[0]["tap_action"]
    assert action["perform_action"] == "bibliotek_dk.renew_all_loans"
    assert action["data"] == {"config_entry_id": "account-a"}
    assert action["confirmation"]["text"]


def test_empty_account_and_unrelated_overdue_account_do_not_create_bulk_control():
    template = cards()["Bibliotekslån"]["filter"]["template"]
    assert render(template, [sensor("loans", []), sensor("loans_overdue", [])]) == []
    assert (
        render(
            template,
            [sensor("loans", []), sensor("loans_overdue", [loan()], entry="account-b")],
        )
        == []
    )
    assert (
        render(
            template,
            [
                sensor("loans", []),
                sensor("loans_overdue", [loan()], state="unavailable"),
            ],
        )
        == []
    )


@pytest.mark.parametrize(
    "title,kind,datefield",
    [
        ("Bibliotekslån overskredet", "loans_overdue", "expire_date"),
        ("Biblioteksreserveringer til afhentning", "reservations_ready", "pickup_date"),
    ],
)
@pytest.mark.parametrize("missing", [False, True])
def test_missing_title_type_date_or_pickup_library_keeps_the_row(
    title, kind, datefield, missing
):
    book = (
        {}
        if missing
        else {
            "title": None,
            "type": None,
            "creators": None,
            datefield: None,
            "pickup_library": None,
            "cover": None,
        }
    )
    result = render(cards()[title]["filter"]["template"], [sensor(kind, [book])])
    assert len(result) == 2
    assert result[1]["name"] == "📖 Titel ukendt"
    assert result[1]["secondary"] == ""
    assert "ukendt" in result[1]["state"]


@pytest.mark.parametrize(
    "title,kind",
    [
        ("Bibliotekslån overskredet", "loans_overdue"),
        ("Biblioteksreserveringer til afhentning", "reservations_ready"),
    ],
)
@pytest.mark.parametrize("state", ["unavailable", "unknown"])
@pytest.mark.parametrize("items", [[], [{"title": "Retained stale data"}]])
def test_unavailable_sources_emit_no_rows_and_show_unavailable_fallback(
    title, kind, state, items
):
    card = cards()[title]
    states = [sensor(kind, items, state=state)]
    assert render(card["filter"]["template"], states) == []
    assert "midlertidigt utilgængelige" in render(card["else"]["content"], states)


@pytest.mark.parametrize(
    "title,kind",
    [
        ("Bibliotekslån overskredet", "loans_overdue"),
        ("Biblioteksreserveringer til afhentning", "reservations_ready"),
    ],
)
def test_available_empty_sources_emit_no_account_section_and_show_empty_message(
    title, kind
):
    card = cards()[title]
    states = [sensor(kind, [], state="0")]
    assert render(card["filter"]["template"], states) == []
    assert "Ingen " in render(card["else"]["content"], states)
    assert "midlertidigt" not in render(card["else"]["content"], states)


def test_unavailable_account_does_not_hide_available_account_rows():
    card = cards()["Bibliotekslån overskredet"]
    states = [
        sensor("loans_overdue", [{"title": "Stale"}], state="unavailable"),
        sensor("loans_overdue", [loan()], entry="account-b"),
    ]
    result = render(card["filter"]["template"], states)
    assert len(result) == 2
    assert result[0]["label"] == "account-b"
    assert result[1]["name"] == "📖 Synthetic title"


def test_individual_confirmation_keeps_title_fallback_and_correct_id():
    book = {**loan(), "title": None, "renewable": True, "renew_id": 42}
    result = render(
        cards()["Bibliotekslån"]["filter"]["template"], [sensor("loans", [book])]
    )
    row = next(row for row in result if row["type"] == "custom:template-entity-row")
    assert row["tap_action"]["data"]["loan_ids"] == [42]
    assert row["tap_action"]["confirmation"]["text"] == "Forny Titel ukendt?"
