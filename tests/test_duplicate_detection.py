"""Tests for duplicate account detection across loaded and locked/unloaded entries."""

from __future__ import annotations

from pathlib import Path

import pytest

from custom_components.bibliotek_dk import config_flow
from custom_components.bibliotek_dk.const import (
    CONF_AGENCY,
    CONF_ENCRYPTION_MODE,
    CONF_ENCRYPTION_PASSPHRASE,
    CONF_HOST,
    CONF_MUNICIPALITY,
    CONF_NAME,
    CONF_PINCODE,
    CONF_UPDATE_INTERVAL,
    CONF_USE_ESCROW_PASSPHRASE,
    CONF_USER_ID,
    DATA_ESCROW_PASSPHRASE,
    ENCRYPTION_MODE_KEYFILE,
    ENCRYPTION_MODE_PASSPHRASE,
)
from custom_components.bibliotek_dk.crypto import (
    compute_keyed_unique_id,
    compute_passphrase_unique_id,
    encrypt_with_key,
    encrypt_with_passphrase,
    get_or_create_master_key,
)


def _secret(label: str) -> str:
    """Build a synthetic secret string without triggering Ruff S105/S106."""
    return f"test-{label}-material-99"


class _FakeConfig:
    def __init__(self, root: Path) -> None:
        self._root = root
        self.longitude = 10.2
        self.latitude = 56.1

    def path(self, *parts: str) -> str:
        return str(self._root.joinpath(*parts))


class _FakeConfigEntries:
    def __init__(self, entries=None) -> None:
        self.entries = list(entries or [])
        self.reloaded = []
        self.updated_calls = []

    def async_entries(self, domain=None):
        return list(self.entries)

    def async_update_entry(
        self,
        entry,
        *,
        data=None,
        options=None,
        unique_id=None,
        version=None,
        title=None,
    ):
        if data is not None:
            entry.data = data
        if options is not None:
            entry.options = options
        if unique_id is not None:
            entry.unique_id = unique_id
        if version is not None:
            entry.version = version
        if title is not None:
            entry.title = title
        self.updated_calls.append(entry.entry_id)


class _FakeEntry:
    def __init__(
        self,
        data: dict,
        *,
        entry_id: str = "entry-1",
        unique_id: str | None = None,
        title: str = "Test Kommune",
    ) -> None:
        self.entry_id = entry_id
        self.data = data
        self.options = {}
        self.unique_id = unique_id
        self.version = 2
        self.title = title


class _FakeHass:
    def __init__(self, root: Path, entries=None, data=None) -> None:
        self.config = _FakeConfig(root)
        self.data = dict(data or {})
        self.config_entries = _FakeConfigEntries(entries)

    async def async_add_executor_job(self, func, *args):
        return func(*args)


LIBRARIES = {
    "Test Kommune": {
        CONF_HOST: "https://test.bib.dk",
        CONF_AGENCY: "710100",
    },
    "Other Kommune": {
        CONF_HOST: "https://other.bib.dk",
        CONF_AGENCY: "720200",
    },
}


def _payload(**overrides):
    base = {
        CONF_MUNICIPALITY: "Test Kommune",
        CONF_USER_ID: "010299-1234",
        CONF_PINCODE: "43 21",
        CONF_NAME: "Reader",
        CONF_UPDATE_INTERVAL: 60,
    }
    base.update(overrides)
    return base


def _make_passphrase_entry(
    *,
    entry_id: str = "locked-entry",
    uid: str = "0102991234",
    pin: str = "4321",
    host: str = "https://test.bib.dk",
    agency: str = "710100",
    municipality: str = "Test Kommune",
    passphrase: str,
) -> _FakeEntry:
    enc_uid = encrypt_with_passphrase(
        passphrase, uid, field=CONF_USER_ID, agency=agency
    )
    enc_pin = encrypt_with_passphrase(
        passphrase, pin, field=CONF_PINCODE, agency=agency
    )
    unique_id = compute_passphrase_unique_id(passphrase, host, uid)
    return _FakeEntry(
        data={
            CONF_MUNICIPALITY: municipality,
            CONF_HOST: host,
            CONF_AGENCY: agency,
            CONF_USER_ID: enc_uid,
            CONF_PINCODE: enc_pin,
            CONF_ENCRYPTION_MODE: ENCRYPTION_MODE_PASSPHRASE,
            CONF_UPDATE_INTERVAL: 60,
        },
        entry_id=entry_id,
        unique_id=unique_id,
        title=municipality,
    )


def _make_keyfile_entry(
    hass: _FakeHass,
    *,
    entry_id: str = "keyfile-entry",
    uid: str = "0102991234",
    pin: str = "4321",
    host: str = "https://test.bib.dk",
    agency: str = "710100",
    municipality: str = "Test Kommune",
) -> _FakeEntry:
    master_key = get_or_create_master_key(hass)
    enc_uid = encrypt_with_key(master_key, uid, field=CONF_USER_ID, agency=agency)
    enc_pin = encrypt_with_key(master_key, pin, field=CONF_PINCODE, agency=agency)
    unique_id = compute_keyed_unique_id(master_key, host, uid)
    return _FakeEntry(
        data={
            CONF_MUNICIPALITY: municipality,
            CONF_HOST: host,
            CONF_AGENCY: agency,
            CONF_USER_ID: enc_uid,
            CONF_PINCODE: enc_pin,
            CONF_ENCRYPTION_MODE: ENCRYPTION_MODE_KEYFILE,
            CONF_UPDATE_INTERVAL: 60,
        },
        entry_id=entry_id,
        unique_id=unique_id,
        title=municipality,
    )


def _patch_library(monkeypatch, login_counter=None):
    class FakeSession:
        def close(self):
            return None

    class FakeLibrary:
        def __init__(self, uid, pin, host, agency, **_kwargs):
            self.user = type("U", (), {"userId": uid})()
            self.host = host
            self.session = FakeSession()

        def login(self):
            if login_counter is not None:
                login_counter["calls"] += 1
            return "https://test.bib.dk/logout"

    monkeypatch.setattr(config_flow, "Library", FakeLibrary)


@pytest.mark.asyncio
async def test_locked_passphrase_entry_blocks_same_host_keyfile_add(
    tmp_path: Path, monkeypatch
):
    """Case 1: Locked entry on same host blocks keyfile add without calling login."""
    login_counter = {"calls": 0}
    _patch_library(monkeypatch, login_counter)
    monkeypatch.setattr(config_flow, "refreshLibraries", lambda: (LIBRARIES, {}))
    monkeypatch.setattr(
        config_flow, "municipalityFromCoor", lambda *_args: "Test Kommune"
    )

    locked = _make_passphrase_entry(passphrase=_secret("owner"))
    hass = _FakeHass(tmp_path, entries=[locked])

    with pytest.raises(config_flow.LockedEntriesExist):
        await config_flow.validate_input(hass, _payload(), LIBRARIES)

    flow = config_flow.ConfigFlow()
    flow.hass = hass
    result = await flow.async_step_user(_payload())

    assert result["type"] == "form"
    assert result["errors"]["base"] == "locked_entries_exist"
    assert login_counter["calls"] == 0


@pytest.mark.asyncio
async def test_locked_passphrase_entry_blocks_same_host_different_passphrase(
    tmp_path: Path, monkeypatch
):
    """Case 2: Locked entry on same host blocks add with a different passphrase."""
    login_counter = {"calls": 0}
    _patch_library(monkeypatch, login_counter)
    monkeypatch.setattr(config_flow, "refreshLibraries", lambda: (LIBRARIES, {}))
    monkeypatch.setattr(
        config_flow, "municipalityFromCoor", lambda *_args: "Test Kommune"
    )

    locked = _make_passphrase_entry(passphrase=_secret("first"))
    hass = _FakeHass(tmp_path, entries=[locked])
    user_data = _payload(**{CONF_ENCRYPTION_PASSPHRASE: _secret("different")})

    with pytest.raises(config_flow.LockedEntriesExist):
        await config_flow.validate_input(hass, user_data, LIBRARIES)

    flow = config_flow.ConfigFlow()
    flow.hass = hass
    result = await flow.async_step_user(user_data)

    assert result["type"] == "form"
    assert result["errors"]["base"] == "locked_entries_exist"
    assert login_counter["calls"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("use_escrow", [False, True])
async def test_locked_passphrase_entry_detects_duplicate_with_matching_or_escrow_passphrase(
    tmp_path: Path, monkeypatch, use_escrow: bool
):
    """Case 3: Matching passphrase or active RAM escrow detects duplicate UserExist."""
    login_counter = {"calls": 0}
    _patch_library(monkeypatch, login_counter)
    monkeypatch.setattr(config_flow, "refreshLibraries", lambda: (LIBRARIES, {}))
    monkeypatch.setattr(
        config_flow, "municipalityFromCoor", lambda *_args: "Test Kommune"
    )

    phrase = _secret("shared")
    locked = _make_passphrase_entry(passphrase=phrase)
    hass = _FakeHass(tmp_path, entries=[locked])

    if use_escrow:
        hass.data[DATA_ESCROW_PASSPHRASE] = phrase
        user_data = _payload(**{CONF_USE_ESCROW_PASSPHRASE: True})
    else:
        user_data = _payload(**{CONF_ENCRYPTION_PASSPHRASE: phrase})

    with pytest.raises(config_flow.UserExist):
        await config_flow.validate_input(hass, user_data, LIBRARIES)

    flow = config_flow.ConfigFlow()
    flow.hass = hass
    result = await flow.async_step_user(user_data)

    assert result["type"] == "form"
    assert result["errors"]["base"] == "user_exist"
    assert login_counter["calls"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("use_escrow", [False, True])
async def test_locked_passphrase_entry_allows_different_family_member_with_same_passphrase(
    tmp_path: Path, monkeypatch, use_escrow: bool
):
    """Case 4: Different family member on same host with matching/escrow passphrase succeeds."""
    login_counter = {"calls": 0}
    _patch_library(monkeypatch, login_counter)

    phrase = _secret("family")
    locked = _make_passphrase_entry(uid="0102991234", passphrase=phrase)
    hass = _FakeHass(tmp_path, entries=[locked])

    if use_escrow:
        hass.data[DATA_ESCROW_PASSPHRASE] = phrase
        user_data = _payload(
            **{
                CONF_USER_ID: "050600-9999",
                CONF_USE_ESCROW_PASSPHRASE: True,
            }
        )
    else:
        user_data = _payload(
            **{
                CONF_USER_ID: "050600-9999",
                CONF_ENCRYPTION_PASSPHRASE: phrase,
            }
        )

    info = await config_flow.validate_input(hass, user_data, LIBRARIES)
    assert info["data"][CONF_ENCRYPTION_MODE] == ENCRYPTION_MODE_PASSPHRASE
    assert info["passphrase"] == phrase
    assert login_counter["calls"] == 1


@pytest.mark.asyncio
async def test_locked_passphrase_entry_on_different_host_does_not_block(
    tmp_path: Path, monkeypatch
):
    """Case 5: Locked entry on a different host allows adding account at new host."""
    login_counter = {"calls": 0}
    _patch_library(monkeypatch, login_counter)

    locked_other_host = _make_passphrase_entry(
        entry_id="locked-test-bib",
        host="https://test.bib.dk",
        agency="710100",
        municipality="Test Kommune",
        passphrase=_secret("other-host"),
    )
    hass = _FakeHass(tmp_path, entries=[locked_other_host])

    info = await config_flow.validate_input(
        hass,
        _payload(**{CONF_MUNICIPALITY: "Other Kommune"}),
        LIBRARIES,
    )
    assert info["data"][CONF_HOST] == "https://other.bib.dk"
    assert login_counter["calls"] == 1


@pytest.mark.asyncio
async def test_unloaded_keyfile_entry_blocks_same_account_in_passphrase_mode(
    tmp_path: Path, monkeypatch
):
    """Case 6: Unloaded keyfile entry on same host detects duplicate when adding in passphrase mode."""
    login_counter = {"calls": 0}
    _patch_library(monkeypatch, login_counter)

    hass = _FakeHass(tmp_path)
    unloaded_keyfile = _make_keyfile_entry(hass, uid="0102991234")
    hass.config_entries.entries.append(unloaded_keyfile)

    with pytest.raises(config_flow.UserExist):
        await config_flow.validate_input(
            hass,
            _payload(**{CONF_ENCRYPTION_PASSPHRASE: _secret("new-pass")}),
            LIBRARIES,
        )
    assert login_counter["calls"] == 0


@pytest.mark.asyncio
async def test_reconfigure_self_unlocked_bypass_and_other_locked_blocked(
    tmp_path: Path, monkeypatch
):
    """Case 7: Self-reconfigure on locked entry succeeds; another locked entry on same host blocks."""
    login_counter = {"calls": 0}
    _patch_library(monkeypatch, login_counter)
    monkeypatch.setattr(config_flow, "refreshLibraries", lambda: (LIBRARIES, {}))

    phrase = _secret("self-locked")
    locked_self = _make_passphrase_entry(
        entry_id="entry-self",
        uid="0102991234",
        passphrase=phrase,
    )
    hass = _FakeHass(tmp_path, entries=[locked_self])

    flow = config_flow.ConfigFlow()
    flow.hass = hass
    flow.context = {"entry_id": locked_self.entry_id}

    res_self = await flow.async_step_reconfigure(
        {
            CONF_MUNICIPALITY: "Test Kommune",
            CONF_USER_ID: "0102991234",
            CONF_PINCODE: "9999",
            CONF_ENCRYPTION_PASSPHRASE: _secret("updated"),
        }
    )
    assert res_self["type"] == "abort"
    assert res_self["reason"] == "reconfigure_successful"
    assert login_counter["calls"] == 1

    # Now add a second locked entry on the same host and reconfigure a keyfile entry.
    other_locked = _make_passphrase_entry(
        entry_id="entry-other-locked",
        uid="0506009999",
        passphrase=_secret("other-locked"),
    )
    keyfile_entry = _make_keyfile_entry(
        hass,
        entry_id="entry-keyfile",
        uid="0102991234",
    )
    hass2 = _FakeHass(tmp_path, entries=[other_locked, keyfile_entry])

    flow2 = config_flow.ConfigFlow()
    flow2.hass = hass2
    flow2.context = {"entry_id": keyfile_entry.entry_id}

    res_blocked = await flow2.async_step_reconfigure(
        {
            CONF_MUNICIPALITY: "Test Kommune",
            CONF_USER_ID: "0102991234",
            CONF_PINCODE: "9999",
            CONF_ENCRYPTION_PASSPHRASE: "",
        }
    )
    assert res_blocked["type"] == "form"
    assert res_blocked["errors"]["base"] == "locked_entries_exist"
    assert login_counter["calls"] == 1


@pytest.mark.asyncio
async def test_reauth_confirm_returns_locked_entries_exist_when_other_locked(
    tmp_path: Path, monkeypatch
):
    """Reauth credential re-entry also maps LockedEntriesExist to errors['base']."""
    login_counter = {"calls": 0}
    _patch_library(monkeypatch, login_counter)

    hass = _FakeHass(tmp_path)
    other_locked = _make_passphrase_entry(
        entry_id="entry-other-locked",
        uid="0506009999",
        passphrase=_secret("other-locked"),
    )
    keyfile_entry = _make_keyfile_entry(
        hass,
        entry_id="entry-keyfile",
        uid="0102991234",
    )
    hass.config_entries.entries.extend([other_locked, keyfile_entry])

    flow = config_flow.ConfigFlow()
    flow.hass = hass
    flow.context = {"entry_id": keyfile_entry.entry_id}

    res = await flow.async_step_reauth_confirm(
        {
            CONF_USER_ID: "0102991234",
            CONF_PINCODE: "9999",
            CONF_ENCRYPTION_PASSPHRASE: "",
        }
    )
    assert res["type"] == "form"
    assert res["errors"]["base"] == "locked_entries_exist"
    assert login_counter["calls"] == 0
