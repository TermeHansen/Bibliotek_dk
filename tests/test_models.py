"""Unit tests for the plain domain models.

These use small, anonymised fixtures shaped like the real DBC responses
(see ``tests/data/``) and never touch the network.
"""

from __future__ import annotations

from custom_components.bibliotek_dk.models import (
    libraryDebt,
    libraryMaterial,
    libraryUser,
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
    assert mat.creators == "Jussi Adler-Olsen"
    assert mat.type == "epub"
    assert mat.url == "https://bibliotek.dk/?q=9788740003826"


def test_ereolen_creators_handle_missing_parts():
    # Regression (plan 7.4): firstName and lastName were concatenated without a
    # separator and a missing key raised inside the guarded block, blanking the
    # whole material.
    data = _ereolen_product()
    data["contributors"] = [{"firstName": "Jussi"}, {"lastName": "Olsen"}]
    # Two contributors are joined the Danish way; parts of one name by a space.
    assert libraryMaterial(data).creators == "Jussi og Olsen"

    data["contributors"] = []
    assert libraryMaterial(data).creators is None

    del data["contributors"]
    assert libraryMaterial(data).creators is None


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


def test_library_user_lists_are_per_instance():
    # Regression: class-level mutable defaults were shared between instances.
    first = libraryUser("1111", "0000")
    second = libraryUser("2222", "0000")
    first.loans.append(object())
    first.debts.append(object())
    assert second.loans == []
    assert second.debts == []
