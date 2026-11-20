"""Unit tests for the credential handling in ``scripts/async_test_module.py``.

The script is a development helper, but its credential precedence is the one
place where a mistake can lock a real patron account out, so it is covered
here. Nothing in this file touches the network.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from scripts import async_test_module as script


def _args(**overrides) -> argparse.Namespace:
    """Build the argparse namespace ``credential_candidates`` reads."""
    base = {
        "user_id": None,
        "pincode": None,
        "secrets": Path("secrets"),
    }
    base.update(overrides)
    return argparse.Namespace(**base)


@pytest.fixture
def secrets_file(tmp_path, monkeypatch):
    """Point the script at a temporary secrets file with two accounts."""

    def _write(text: str) -> Path:
        path = tmp_path / "secrets"
        path.write_text(text, encoding="utf-8")
        return path

    return _write


def test_both_flags_win_outright(secrets_file):
    path = secrets_file("1111111111 1111\n2222222222 2222\n")

    candidates = script.credential_candidates(
        _args(user_id="3333333333", pincode="3333", secrets=path)
    )

    assert candidates == [("3333333333", "3333", "flags")]


def test_a_lone_pincode_overrides_the_stored_one(secrets_file):
    # Regression: a supplied PIN used to be ignored whenever the secrets file
    # had entries, so a developer correcting a stale PIN still sent the saved
    # one — and a rejected PIN can lock the account. The id is *not* supplied,
    # so the stored lines are the candidates and only the PIN is overridden.
    path = secrets_file("1111111111 1111\n2222222222 2222\n")

    candidates = script.credential_candidates(_args(pincode="9999", secrets=path))

    assert [(user_id, pin) for user_id, pin, _ in candidates] == [
        ("1111111111", "9999"),
        ("2222222222", "9999"),
    ]


def test_a_supplied_id_and_pin_beat_the_secrets_file(secrets_file):
    path = secrets_file("1111111111 1111\n")

    candidates = script.credential_candidates(
        _args(user_id="1111111111", pincode="9999", secrets=path)
    )

    assert candidates == [("1111111111", "9999", "flags")]


def test_a_lone_pincode_applies_to_every_secrets_candidate(secrets_file):
    path = secrets_file("1111111111 1111\n2222222222 2222\n")

    candidates = script.credential_candidates(_args(pincode="9999", secrets=path))

    assert [(user_id, pin) for user_id, pin, _ in candidates] == [
        ("1111111111", "9999"),
        ("2222222222", "9999"),
    ]


def test_a_user_id_alone_selects_only_its_own_lines(secrets_file):
    # Never pair an id with another account's PIN.
    path = secrets_file("1111111111 1111\n2222222222 2222\n")

    candidates = script.credential_candidates(_args(user_id="2222222222", secrets=path))

    assert [(user_id, pin) for user_id, pin, _ in candidates] == [
        ("2222222222", "2222")
    ]


def test_every_usable_line_is_a_candidate(secrets_file):
    path = secrets_file("# comment\n1111111111 1111\n\n2222222222 2222\n")

    candidates = script.credential_candidates(_args(secrets=path))

    assert [user_id for user_id, _, _ in candidates] == [
        "1111111111",
        "2222222222",
    ]


def test_conflicting_pincodes_flags_an_id_with_two_pins():
    # The one thing the script could do to cause a lockout by itself.
    conflicts = script.conflicting_pincodes(
        [("1111", "1234", "line 1"), ("1111", "9999", "line 2")]
    )

    assert "1111" in conflicts
    assert len(conflicts["1111"]) == 2


def test_conflicting_pincodes_is_empty_for_consistent_input():
    assert (
        script.conflicting_pincodes([("1111", "1234", "a"), ("2222", "5678", "b")])
        == {}
    )


class _FailingLogoutLibrary:
    """A library whose server-side logout raises, as a network failure would."""

    def __init__(self):
        self.closed = False

    def logout(self):
        raise script.RequestException("logout blew up")

    class _Session:
        def __init__(self, owner):
            self.owner = owner

        def close(self):
            self.owner.closed = True

    @property
    def session(self):
        return self._Session(self)


async def test_close_library_never_raises_and_still_closes_the_session():
    # Regression: an exception in logout() escaped the `finally` in
    # fetch_account(), replacing the account's result (masking a login error or
    # a good snapshot) and stopping the remaining accounts in the run.
    library = _FailingLogoutLibrary()

    await script.close_library(library)

    assert library.closed is True
