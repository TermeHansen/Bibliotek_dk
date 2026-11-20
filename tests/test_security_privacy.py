"""Regression tests for CPR encryption, Reauth/Options isolation, migration, and PII privacy."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import pytest

from custom_components.bibliotek_dk import (
    async_migrate_entry,
    async_remove_entry,
    async_setup_entry,
    async_unload_entry,
    config_flow,
)
from custom_components.bibliotek_dk.const import (
    CONF_AGENCY,
    CONF_ENCRYPTION_MODE,
    CONF_ENCRYPTION_PASSPHRASE,
    CONF_EXPOSE_PERSONAL_INFO,
    CONF_HOST,
    CONF_MUNICIPALITY,
    CONF_NAME,
    CONF_PINCODE,
    CONF_SHOW_DEBTS,
    CONF_SHOW_ELOANS,
    CONF_SHOW_LOANS,
    CONF_SHOW_RESERVATIONS,
    CONF_UPDATE_INTERVAL,
    CONF_USER_ID,
    DATA_PASSPHRASES,
    DOMAIN,
    ENCRYPTION_MODE_KEYFILE,
    ENCRYPTION_MODE_PASSPHRASE,
)
from custom_components.bibliotek_dk.crypto import (
    ENC_PREFIX_KEYFILE,
    ENC_PREFIX_PASSPHRASE,
    decrypt_with_key,
    get_or_create_master_key,
)
from custom_components.bibliotek_dk.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.bibliotek_dk.library_api import Library, LibraryAuthError
from custom_components.bibliotek_dk.sensor import (
    DebtSensor,
    LibrarySensor,
    LoanOverdueSensor,
    LoanSensor,
    ReservationReadySensor,
    ReservationSensor,
)


def _secret(label: str) -> str:
    """Build a synthetic secret string without triggering Ruff S105/S106."""
    return f"test-{label}-secret-42"


class _FakeConfig:
    def __init__(self, root: Path) -> None:
        self._root = root
        self.longitude = 10.2
        self.latitude = 56.1

    def path(self, *parts: str) -> str:
        return str(self._root.joinpath(*parts))


class _FakeServices:
    def __init__(self) -> None:
        self.registered = {}

    def has_service(self, domain, service):
        return (domain, service) in self.registered

    def async_register(self, domain, service, handler, supports_response=None):
        self.registered[(domain, service)] = handler

    def async_remove(self, domain, service):
        self.registered.pop((domain, service), None)


class _FakeConfigEntries:
    def __init__(self, entries=None) -> None:
        self.entries = list(entries or [])
        self.reloaded = []
        self.updated_calls = []

    async def async_forward_entry_setups(self, entry, platforms):
        return True

    async def async_unload_platforms(self, entry, platforms):
        return True

    async def async_reload(self, entry_id):
        self.reloaded.append(entry_id)
        return True

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
        entry_id: str = "entry-abc-1234",
        options: dict | None = None,
        unique_id: str | None = None,
        version: int = 2,
        title: str = "Aarhus",
    ) -> None:
        self.entry_id = entry_id
        self.data = data
        self.options = dict(options or {})
        self.unique_id = unique_id
        self.version = version
        self.title = title

    def async_on_unload(self, func):
        return None

    def add_update_listener(self, func):
        return func


class _FakeHass:
    def __init__(self, root: Path, entries=None) -> None:
        from homeassistant.helpers import entity_registry as er

        self.config = _FakeConfig(root)
        self.data = {}
        self.services = _FakeServices()
        self.config_entries = _FakeConfigEntries(entries)
        self._entity_registry = er.EntityRegistry()

    async def async_add_executor_job(self, func, *args):
        return func(*args)


LIBRARIES = {
    "Test Kommune": {
        CONF_HOST: "https://test.bib.dk",
        CONF_AGENCY: "710100",
    }
}


def _user_payload(**overrides):
    base = {
        CONF_MUNICIPALITY: "Test Kommune",
        CONF_USER_ID: "010299-1234",
        CONF_PINCODE: "43 21",
        CONF_NAME: "Reader",
        CONF_UPDATE_INTERVAL: 60,
        CONF_SHOW_LOANS: True,
        CONF_SHOW_ELOANS: True,
        CONF_SHOW_DEBTS: True,
        CONF_SHOW_RESERVATIONS: True,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_validate_input_encrypts_keyfile_and_closes_session(
    tmp_path: Path, monkeypatch
):
    closed = {"count": 0}

    class FakeSession:
        def close(self):
            closed["count"] += 1

    class FakeLibrary:
        def __init__(self, uid, pin, host, agency, **_kwargs):
            self.user = type("U", (), {"userId": uid})()
            self.host = host
            self.session = FakeSession()

        def login(self):
            return "https://test.bib.dk/logout"

    monkeypatch.setattr(config_flow, "Library", FakeLibrary)
    hass = _FakeHass(tmp_path)

    info = await config_flow.validate_input(hass, _user_payload(), LIBRARIES)

    assert closed["count"] == 1
    assert info["data"][CONF_ENCRYPTION_MODE] == ENCRYPTION_MODE_KEYFILE
    assert info["data"][CONF_USER_ID].startswith(ENC_PREFIX_KEYFILE)
    assert info["data"][CONF_PINCODE].startswith(ENC_PREFIX_KEYFILE)
    assert CONF_ENCRYPTION_PASSPHRASE not in info["data"]
    assert "0102991234" not in str(info["data"])
    assert "4321" not in str(info["data"])
    assert info["unique_id"].startswith("hmac_v1:")
    assert "0102991234" not in info["unique_id"]

    # Validation failure also closes the temporary session.
    class FailingLibrary(FakeLibrary):
        def login(self):
            return ""

    monkeypatch.setattr(config_flow, "Library", FailingLibrary)
    with pytest.raises(config_flow.InvalidAuth):
        await config_flow.validate_input(hass, _user_payload(), LIBRARIES)
    assert closed["count"] == 2


@pytest.mark.asyncio
async def test_validate_input_passphrase_mode_never_persists_passphrase(
    tmp_path: Path, monkeypatch
):
    class FakeSession:
        def close(self):
            return None

    class FakeLibrary:
        def __init__(self, uid, pin, host, agency, **_kwargs):
            self.user = type("U", (), {"userId": uid})()
            self.host = host
            self.session = FakeSession()

        def login(self):
            return "https://test.bib.dk/logout"

    monkeypatch.setattr(config_flow, "Library", FakeLibrary)
    hass = _FakeHass(tmp_path)
    phrase = _secret("user")
    payload = _user_payload(**{CONF_ENCRYPTION_PASSPHRASE: phrase})

    info = await config_flow.validate_input(hass, payload, LIBRARIES)

    assert info["data"][CONF_ENCRYPTION_MODE] == ENCRYPTION_MODE_PASSPHRASE
    assert info["data"][CONF_USER_ID].startswith(ENC_PREFIX_PASSPHRASE)
    assert info["data"][CONF_PINCODE].startswith(ENC_PREFIX_PASSPHRASE)
    assert CONF_ENCRYPTION_PASSPHRASE not in info["data"]
    assert info["passphrase"] == phrase
    assert info["unique_id"].startswith("hmac_p1:")


@pytest.mark.asyncio
async def test_config_flow_masks_sensitive_inputs_with_password_selector(
    tmp_path: Path, monkeypatch
):
    from homeassistant.helpers import selector

    monkeypatch.setattr(config_flow, "refreshLibraries", lambda: (LIBRARIES, {}))
    monkeypatch.setattr(
        config_flow, "municipalityFromCoor", lambda *_args: "Test Kommune"
    )

    flow = config_flow.ConfigFlow()
    flow.hass = _FakeHass(tmp_path)

    result = await flow.async_step_user(None)
    schema = result["data_schema"].schema

    for key in (CONF_USER_ID, CONF_PINCODE, CONF_ENCRYPTION_PASSPHRASE):
        field = schema[key]
        assert isinstance(field, selector.TextSelector)
        assert field.config.type == selector.TextSelectorType.PASSWORD

    assert CONF_EXPOSE_PERSONAL_INFO in schema


@pytest.mark.asyncio
async def test_options_flow_never_copies_credentials_to_options(tmp_path: Path):
    entry = _FakeEntry(
        data={
            CONF_USER_ID: "enc:v1:opaque",
            CONF_PINCODE: "enc:v1:opaque",
            CONF_SHOW_LOANS: True,
            CONF_SHOW_ELOANS: True,
            CONF_SHOW_DEBTS: True,
            CONF_SHOW_RESERVATIONS: True,
            CONF_EXPOSE_PERSONAL_INFO: False,
            CONF_UPDATE_INTERVAL: 60,
        }
    )
    flow = config_flow.OptionsFlow()
    flow.hass = _FakeHass(tmp_path, [entry])
    flow.config_entry = entry

    res = await flow.async_step_init(
        {
            CONF_SHOW_LOANS: False,
            CONF_SHOW_ELOANS: True,
            CONF_SHOW_DEBTS: True,
            CONF_SHOW_RESERVATIONS: False,
            CONF_EXPOSE_PERSONAL_INFO: True,
            CONF_UPDATE_INTERVAL: 30,
        }
    )

    assert res["type"] == "create_entry"
    saved = res["data"]
    assert CONF_USER_ID not in saved
    assert CONF_PINCODE not in saved
    assert saved == {
        CONF_SHOW_LOANS: False,
        CONF_SHOW_ELOANS: True,
        CONF_SHOW_DEBTS: True,
        CONF_SHOW_RESERVATIONS: False,
        CONF_EXPOSE_PERSONAL_INFO: True,
        CONF_UPDATE_INTERVAL: 30,
    }


@pytest.mark.asyncio
async def test_passphrase_mode_requires_reauth_after_restart_and_unlocks(
    tmp_path: Path, monkeypatch
):
    from homeassistant.exceptions import ConfigEntryAuthFailed

    class FakeSession:
        def close(self):
            return None

    class FakeLibrary:
        def __init__(self, uid, pin, host, agency, **_kwargs):
            self.user = type("U", (), {"userId": uid})()
            self.host = host
            self.session = FakeSession()

        def login(self):
            return "https://test.bib.dk/logout"

    monkeypatch.setattr(config_flow, "Library", FakeLibrary)
    hass = _FakeHass(tmp_path)
    phrase = _secret("unlock")
    info = await config_flow.validate_input(
        hass, _user_payload(**{CONF_ENCRYPTION_PASSPHRASE: phrase}), LIBRARIES
    )
    entry = _FakeEntry(
        data=info["data"],
        unique_id=info["unique_id"],
        title=info["title"],
    )
    hass.config_entries.entries.append(entry)

    # Without passphrase in RAM (fresh restart), setup raises ConfigEntryAuthFailed.
    with pytest.raises(ConfigEntryAuthFailed):
        await async_setup_entry(hass, entry)

    # Reauth rejects wrong passphrase.
    flow = config_flow.ConfigFlow()
    flow.hass = hass
    flow.context = {"entry_id": entry.entry_id}
    res_bad = await flow.async_step_reauth_confirm(
        {CONF_ENCRYPTION_PASSPHRASE: _secret("bad")}
    )
    assert res_bad["type"] == "form"
    assert res_bad["errors"]["base"] == "invalid_passphrase"

    # Valid passphrase stores secret in RAM and reloads entry.
    res_ok = await flow.async_step_reauth_confirm({CONF_ENCRYPTION_PASSPHRASE: phrase})
    assert res_ok["type"] == "abort"
    assert res_ok["reason"] == "reauth_successful"
    assert hass.data[DATA_PASSPHRASES][entry.entry_id] == phrase

    assert await async_setup_entry(hass, entry) is True
    assert hass.data[DOMAIN][entry.entry_id].user.userId == "0102991234"

    # Unload (used during options reload) retains RAM passphrase; remove purges it.
    assert await async_unload_entry(hass, entry) is True
    assert entry.entry_id in hass.data[DATA_PASSPHRASES]

    await async_remove_entry(hass, entry)
    assert entry.entry_id not in hass.data[DATA_PASSPHRASES]


@pytest.mark.asyncio
async def test_migrate_v1_to_v2_encrypts_credentials_strips_options_and_migrates_entities(
    tmp_path: Path,
):
    from homeassistant.helpers import entity_registry as er

    hass = _FakeHass(tmp_path)
    cpr = "0102991234"
    pin = "4321"
    entry = _FakeEntry(
        data={
            CONF_USER_ID: cpr,
            CONF_PINCODE: pin,
            CONF_HOST: "https://test.bib.dk",
            CONF_AGENCY: "710100",
            CONF_MUNICIPALITY: "Test Kommune",
            CONF_SHOW_LOANS: True,
            CONF_SHOW_DEBTS: True,
            CONF_SHOW_RESERVATIONS: True,
            CONF_UPDATE_INTERVAL: 60,
        },
        options={
            CONF_USER_ID: cpr,
            CONF_PINCODE: pin,
            CONF_SHOW_LOANS: False,
            CONF_UPDATE_INTERVAL: 30,
        },
        unique_id=cpr,
        version=1,
    )
    hass.config_entries.entries.append(entry)

    def _md5(s: str) -> str:
        return hashlib.md5(s.encode("utf-8"), usedforsecurity=False).hexdigest()

    reg = er.async_get(hass)
    reg.entities = {
        "sensor.loans": er.RegistryEntry(
            "sensor.loans", _md5("Loans_" + cpr), entry.entry_id
        ),
        "sensor.overdue": er.RegistryEntry(
            "sensor.overdue", _md5("LoansOverdue_" + cpr), entry.entry_id
        ),
        "sensor.debts": er.RegistryEntry(
            "sensor.debts", _md5("Debts_" + cpr), entry.entry_id
        ),
        "sensor.res": er.RegistryEntry(
            "sensor.res", _md5("Reservations_" + cpr), entry.entry_id
        ),
        "sensor.ready": er.RegistryEntry(
            "sensor.ready", _md5("ReservationsReady_" + cpr), entry.entry_id
        ),
        "sensor.main": er.RegistryEntry(
            "sensor.main", _md5("Hovedbiblioteket" + cpr), entry.entry_id
        ),
    }

    assert await async_migrate_entry(hass, entry) is True
    assert entry.version == 2
    assert entry.data[CONF_ENCRYPTION_MODE] == ENCRYPTION_MODE_KEYFILE
    assert entry.data[CONF_USER_ID].startswith(ENC_PREFIX_KEYFILE)
    assert entry.data[CONF_PINCODE].startswith(ENC_PREFIX_KEYFILE)
    assert entry.unique_id.startswith("hmac_v1:")
    assert cpr not in entry.unique_id

    master = get_or_create_master_key(hass)
    assert (
        decrypt_with_key(
            master, entry.data[CONF_USER_ID], field=CONF_USER_ID, agency="710100"
        )
        == cpr
    )
    assert (
        decrypt_with_key(
            master, entry.data[CONF_PINCODE], field=CONF_PINCODE, agency="710100"
        )
        == pin
    )

    # Leaked credentials stripped from options.
    assert CONF_USER_ID not in entry.options
    assert CONF_PINCODE not in entry.options
    assert entry.options[CONF_SHOW_LOANS] is False
    assert entry.options[CONF_UPDATE_INTERVAL] == 30

    # All entity registry unique IDs migrated away from unsalted MD5.
    assert reg.entities["sensor.loans"].unique_id == f"{entry.entry_id}_loans"
    assert reg.entities["sensor.overdue"].unique_id == f"{entry.entry_id}_loans_overdue"
    assert reg.entities["sensor.debts"].unique_id == f"{entry.entry_id}_debts"
    assert reg.entities["sensor.res"].unique_id == f"{entry.entry_id}_reservations"
    assert (
        reg.entities["sensor.ready"].unique_id == f"{entry.entry_id}_reservations_ready"
    )
    assert reg.entities["sensor.main"].unique_id == f"{entry.entry_id}_main"


def test_sensors_use_opaque_unique_ids_and_gate_personal_info():
    cpr = "0102991234"
    lib = Library(
        cpr,
        "4321",
        "https://example.invalid",
        "710100",
        libraryName="Aarhus",
        account_label="Aarhus:entry-ab",
    )
    lib.user.name = "Test Person"
    lib.user.address = "Hemmelig Vej 1\n8000 Aarhus C"
    lib.user.phone = "+4512345678"
    lib.user.mail = "person@example.invalid"
    lib.libraryName = "Dokk1"

    coord = type("C", (), {"last_update_success": True})()
    entry_id = "entry-abc-1234"

    sensors = [
        LibrarySensor(lib, coord, entry_id=entry_id),
        LoanSensor(lib.user, coord, entry_id=entry_id),
        LoanOverdueSensor(lib.user, coord, entry_id=entry_id),
        DebtSensor(lib.user, coord, entry_id=entry_id),
        ReservationSensor(lib.user, coord, entry_id=entry_id),
        ReservationReadySensor(lib.user, coord, entry_id=entry_id),
    ]

    md5_loans = hashlib.md5(
        ("Loans_" + cpr).encode("utf-8"), usedforsecurity=False
    ).hexdigest()
    uids = [s.unique_id for s in sensors]
    assert uids == [
        f"{entry_id}_main",
        f"{entry_id}_loans",
        f"{entry_id}_loans_overdue",
        f"{entry_id}_debts",
        f"{entry_id}_reservations",
        f"{entry_id}_reservations_ready",
    ]
    assert all(md5_loans != u and cpr not in u for u in uids)

    # Personal PII attributes omitted by default.
    attrs_default = sensors[0].extra_state_attributes
    assert "address" not in attrs_default
    assert "phone" not in attrs_default
    assert "mail" not in attrs_default
    assert attrs_default["sensor_type"] == "main"

    # Included only when explicitly opted in.
    sensor_opt_in = LibrarySensor(
        lib, coord, entry_id=entry_id, expose_personal_info=True
    )
    attrs_opt_in = sensor_opt_in.extra_state_attributes
    assert attrs_opt_in["address"] == "Hemmelig Vej 1\n8000 Aarhus C"
    assert attrs_opt_in["phone"] == "+4512345678"
    assert attrs_opt_in["mail"] == "person@example.invalid"


def test_no_cpr_or_birthdate_leaks_in_logs_errors_or_repr(caplog):
    cpr = "0102991234"
    pin = "4321"
    lib = Library(
        cpr,
        pin,
        "https://example.invalid",
        "710100",
        libraryName="Aarhus",
        account_label="Aarhus:entry-ab",
    )

    assert cpr not in repr(lib.user)
    assert "010299" not in repr(lib.user)
    assert pin not in repr(lib.user)
    assert not hasattr(lib.user, "date") or getattr(lib.user, "date", None) != "010299"

    lib._transport.login = lambda: ""
    with caplog.at_level(logging.DEBUG, logger="custom_components.bibliotek_dk"):
        with pytest.raises(LibraryAuthError) as exc_info:
            lib.update()

    combined = "\n".join(rec.getMessage() for rec in caplog.records) + str(
        exc_info.value
    )
    assert cpr not in combined
    assert "010299" not in combined
    assert "1234" not in combined
    assert pin not in combined


@pytest.mark.asyncio
async def test_diagnostics_redacts_all_pii_and_reports_encryption_mode(tmp_path: Path):
    cpr = "0102991234"
    lib = Library(cpr, "4321", "https://example.invalid", "710100")
    lib.user.name = "Test User"
    lib.user.address = "Hemmelig Vej 1"
    lib.user.phone = "12345678"
    lib.user.mail = "test@example.invalid"

    hass = _FakeHass(tmp_path)
    hass.data[DOMAIN] = {"entry1": lib}
    entry = _FakeEntry(
        data={
            CONF_USER_ID: "enc:v1:abc",
            CONF_PINCODE: "enc:v1:def",
            CONF_NAME: "Home account",
            CONF_ENCRYPTION_MODE: ENCRYPTION_MODE_KEYFILE,
        },
        entry_id="entry1",
    )

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert diag["encryption_mode"] == ENCRYPTION_MODE_KEYFILE
    assert diag["entry"][CONF_USER_ID] == "**REDACTED**"
    assert diag["entry"][CONF_PINCODE] == "**REDACTED**"
    assert diag["entry"][CONF_NAME] == "**REDACTED**"
    assert diag["entry"]["unique_id"] == "**REDACTED**"
    assert diag["account"]["name"] == "**REDACTED**"
    assert diag["account"]["address"] == "**REDACTED**"
    assert diag["account"]["phone"] == "**REDACTED**"
    assert diag["account"]["mail"] == "**REDACTED**"


@pytest.mark.asyncio
async def test_reconfigure_switches_encryption_modes(tmp_path: Path, monkeypatch):
    class FakeSession:
        def close(self):
            return None

    class FakeLibrary:
        def __init__(self, uid, pin, host, agency, **_kwargs):
            self.user = type("U", (), {"userId": uid})()
            self.host = host
            self.session = FakeSession()

        def login(self):
            return "https://test.bib.dk/logout"

    monkeypatch.setattr(config_flow, "Library", FakeLibrary)
    monkeypatch.setattr(config_flow, "refreshLibraries", lambda: (LIBRARIES, {}))

    hass = _FakeHass(tmp_path)
    info = await config_flow.validate_input(hass, _user_payload(), LIBRARIES)
    entry = _FakeEntry(data=info["data"], unique_id=info["unique_id"])
    hass.config_entries.entries.append(entry)

    flow = config_flow.ConfigFlow()
    flow.hass = hass
    flow.context = {"entry_id": entry.entry_id}

    phrase = _secret("reconf")
    res1 = await flow.async_step_reconfigure(
        {
            CONF_MUNICIPALITY: "Test Kommune",
            CONF_USER_ID: "0102991234",
            CONF_PINCODE: "9999",
            CONF_ENCRYPTION_PASSPHRASE: phrase,
        }
    )
    assert res1["type"] == "abort"
    assert res1["reason"] == "reconfigure_successful"
    assert entry.data[CONF_ENCRYPTION_MODE] == ENCRYPTION_MODE_PASSPHRASE
    assert entry.data[CONF_USER_ID].startswith(ENC_PREFIX_PASSPHRASE)
    assert hass.data[DATA_PASSPHRASES][entry.entry_id] == phrase

    res2 = await flow.async_step_reconfigure(
        {
            CONF_MUNICIPALITY: "Test Kommune",
            CONF_USER_ID: "0102991234",
            CONF_PINCODE: "9999",
            CONF_ENCRYPTION_PASSPHRASE: "",
        }
    )
    assert res2["type"] == "abort"
    assert res2["reason"] == "reconfigure_successful"
    assert entry.data[CONF_ENCRYPTION_MODE] == ENCRYPTION_MODE_KEYFILE
    assert entry.data[CONF_USER_ID].startswith(ENC_PREFIX_KEYFILE)
    assert entry.entry_id not in hass.data[DATA_PASSPHRASES]
