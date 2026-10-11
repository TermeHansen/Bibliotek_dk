"""Unit tests for the HTTP transport, login and token handling."""

from __future__ import annotations

from custom_components.bibliotek_dk.models import libraryUser
from custom_components.bibliotek_dk.transport import REQUEST_TIMEOUT, LibraryTransport


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


def _transport(session=None):
    transport = LibraryTransport("https://example.invalid", libraryUser("1234", "0000"))
    if session is not None:
        transport.session = session
    return transport


def _tok(prefix):
    """Build a fake token without a password-shaped literal (ruff S105)."""
    return prefix + "0123456789abcdef"


def test_session_is_configured_with_timeout_and_retries():
    transport = _transport()
    adapter = transport.session.get_adapter("https://example.invalid")
    assert adapter.max_retries.total == 3
    assert 500 in adapter.max_retries.status_forcelist
    # Exhausted status retries must return the response, not raise RetryError,
    # so callers' status-code checks still run.
    assert adapter.max_retries.raise_on_status is False
    # The helpers apply a default timeout when the caller does not pass one.
    assert REQUEST_TIMEOUT == (10, 30)


def test_get_and_post_apply_default_timeout():
    transport = _transport(_FakeSession(post=_FakeResponse(), get=_FakeResponse()))
    transport._get("https://example.invalid/x")
    assert transport.session.last_kwargs["timeout"] == REQUEST_TIMEOUT
    transport._post("https://example.invalid/x")
    assert transport.session.last_kwargs["timeout"] == REQUEST_TIMEOUT


def test_token_expiry_is_none_before_first_login():
    # Regression: user_token compared an attribute that was never set.
    transport = _transport()
    assert transport._user_token_exp is None


def test_set_tokens_parses_library_and_user_token():
    library_tok, user_tok = _tok("lib"), _tok("user")
    text = f'{{"library":"{library_tok}","user":"{user_tok}"}}'
    transport = _transport(_FakeSession(get=_FakeResponse(text=text)))
    transport._set_tokens()
    assert transport._library_token == library_tok
    assert transport._user_token == user_tok
    assert transport.loggedIn == "https://example.invalid/logout"
    assert transport._user_token_exp is not None


def test_set_tokens_library_only_leaves_user_logged_out():
    library_tok = _tok("lib")
    text = f'{{"library":"{library_tok}"}}'
    transport = _transport(_FakeSession(get=_FakeResponse(text=text)))
    transport._set_tokens()
    assert transport._library_token == library_tok
    assert transport._user_token == ""
    assert transport.loggedIn == ""


def test_set_tokens_warns_on_non_200(caplog):
    import logging

    transport = _transport(_FakeSession(get=_FakeResponse(status_code=500)))
    with caplog.at_level(logging.WARNING):
        transport._set_tokens()
    assert transport._library_token == ""
    assert any("user tokens" in r.getMessage() for r in caplog.records)
