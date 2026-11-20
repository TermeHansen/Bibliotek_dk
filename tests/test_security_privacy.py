"""Regression tests for CPR encryption, Reauth/Options isolation, migration, and PII privacy."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import pytest

from custom_components.bibliotek_dk import (
    async_migrate_entry,
    async_remove_entry,
    async_setup,
    async_setup_entry,
    async_unload_entry,
    config_flow,
)
from custom_components.bibliotek_dk.const import (
    CONF_AGENCY,
    CONF_CONFIG_ENTRY_ID,
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
    CONF_USE_ESCROW_PASSPHRASE,
    CONF_USER_ID,
    DATA_ESCROW_PASSPHRASE,
    DATA_PASSPHRASES,
    DOMAIN,
    ENCRYPTION_MODE_KEYFILE,
    ENCRYPTION_MODE_PASSPHRASE,
    SERVICE_UNLOCK,
)
from custom_components.bibliotek_dk.crypto import (
    ENC_PREFIX_KEYFILE,
    ENC_PREFIX_PASSPHRASE,
    decrypt_with_key,
    encrypt_with_key,
    encrypt_with_passphrase,
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
        self.supports_response = {}

    def has_service(self, domain, service):
        return (domain, service) in self.registered

    def async_register(self, domain, service, handler, supports_response=None):
        self.registered[(domain, service)] = handler
        self.supports_response[(domain, service)] = supports_response

    def async_remove(self, domain, service):
        self.registered.pop((domain, service), None)
        self.supports_response.pop((domain, service), None)


class _FakeConfigEntries:
    def __init__(self, entries=None) -> None:
        self.entries = list(entries or [])
        self.reloaded = []
        self.updated_calls = []

    def async_entries(self, domain=None):
        return list(self.entries)

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


@pytest.mark.asyncio
async def test_async_setup_registers_unlock_service_with_zero_entries(tmp_path: Path):
    from homeassistant.core import SupportsResponse

    hass = _FakeHass(tmp_path)
    assert await async_setup(hass, {}) is True
    assert (DOMAIN, SERVICE_UNLOCK) in hass.services.registered
    assert (
        hass.services.supports_response[(DOMAIN, SERVICE_UNLOCK)]
        == SupportsResponse.OPTIONAL
    )


@pytest.mark.asyncio
async def test_unlock_service_stores_escrow_unlocks_multiple_family_entries(
    tmp_path: Path, monkeypatch
):
    from homeassistant.core import ServiceCall
    from homeassistant.exceptions import HomeAssistantError

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
    assert await async_setup(hass, {}) is True

    phrase = _secret("family-escrow")
    other_phrase = _secret("other-member")

    info1 = await config_flow.validate_input(
        hass,
        _user_payload(
            **{CONF_USER_ID: "0102991234", CONF_ENCRYPTION_PASSPHRASE: phrase}
        ),
        LIBRARIES,
    )
    info2 = await config_flow.validate_input(
        hass,
        _user_payload(
            **{CONF_USER_ID: "0203005678", CONF_ENCRYPTION_PASSPHRASE: phrase}
        ),
        LIBRARIES,
    )
    info3 = await config_flow.validate_input(
        hass,
        _user_payload(**{CONF_USER_ID: "0304019999"}),
        LIBRARIES,
    )
    info4 = await config_flow.validate_input(
        hass,
        _user_payload(
            **{CONF_USER_ID: "0405021111", CONF_ENCRYPTION_PASSPHRASE: other_phrase}
        ),
        LIBRARIES,
    )

    e1 = _FakeEntry(
        data=info1["data"], entry_id="family_1", unique_id=info1["unique_id"]
    )
    e2 = _FakeEntry(
        data=info2["data"], entry_id="family_2", unique_id=info2["unique_id"]
    )
    e3 = _FakeEntry(
        data=info3["data"], entry_id="family_3", unique_id=info3["unique_id"]
    )
    e4 = _FakeEntry(
        data=info4["data"], entry_id="family_4", unique_id=info4["unique_id"]
    )
    hass.config_entries.entries.extend([e1, e2, e3, e4])

    unlock_handler = hass.services.registered[(DOMAIN, SERVICE_UNLOCK)]
    res = await unlock_handler(ServiceCall({CONF_ENCRYPTION_PASSPHRASE: phrase}))

    assert hass.data[DATA_ESCROW_PASSPHRASE] == phrase
    assert hass.data[DATA_PASSPHRASES]["family_1"] == phrase
    assert hass.data[DATA_PASSPHRASES]["family_2"] == phrase
    assert hass.config_entries.reloaded == ["family_1", "family_2"]
    assert res == {
        "escrow_active": True,
        "unlocked": ["family_1", "family_2"],
        "already_unlocked": ["family_3"],
        "failed": ["family_4"],
    }
    assert phrase not in str(res)

    # Mark family_1 as already loaded; repeated unlock skips it cleanly.
    hass.config_entries.reloaded.clear()
    hass.data.setdefault(DOMAIN, {})["family_1"] = object()
    res_repeat = await unlock_handler(ServiceCall({CONF_ENCRYPTION_PASSPHRASE: phrase}))
    assert "family_1" in res_repeat["already_unlocked"]
    assert "family_1" not in res_repeat["unlocked"]
    assert hass.config_entries.reloaded == ["family_2"]

    # Explicit config_entry_id with wrong passphrase raises HomeAssistantError.
    with pytest.raises(HomeAssistantError):
        await unlock_handler(
            ServiceCall(
                {
                    CONF_ENCRYPTION_PASSPHRASE: _secret("wrong"),
                    "config_entry_id": "family_1",
                }
            )
        )


@pytest.mark.asyncio
async def test_async_setup_entry_falls_back_to_ram_escrow_passphrase(
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
    phrase = _secret("escrow-fallback")

    info = await config_flow.validate_input(
        hass, _user_payload(**{CONF_ENCRYPTION_PASSPHRASE: phrase}), LIBRARIES
    )
    entry = _FakeEntry(
        data=info["data"], entry_id="escrow_entry", unique_id=info["unique_id"]
    )
    hass.data[DATA_ESCROW_PASSPHRASE] = phrase

    assert await async_setup_entry(hass, entry) is True
    assert hass.data[DOMAIN]["escrow_entry"].user.userId == "0102991234"
    assert hass.data[DATA_PASSPHRASES]["escrow_entry"] == phrase


@pytest.mark.asyncio
async def test_config_flow_uses_ram_escrow_passphrase_without_persisting_it(
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
    monkeypatch.setattr(config_flow, "refreshLibraries", lambda: (LIBRARIES, {}))
    monkeypatch.setattr(
        config_flow, "municipalityFromCoor", lambda *_args: "Test Kommune"
    )

    hass = _FakeHass(tmp_path)
    phrase = _secret("active-escrow")
    hass.data[DATA_ESCROW_PASSPHRASE] = phrase

    flow = config_flow.ConfigFlow()
    flow.hass = hass
    form = await flow.async_step_user(None)
    assert CONF_USE_ESCROW_PASSPHRASE in form["data_schema"].schema

    info = await config_flow.validate_input(
        hass, _user_payload(**{CONF_USE_ESCROW_PASSPHRASE: True}), LIBRARIES
    )
    assert info["data"][CONF_ENCRYPTION_MODE] == ENCRYPTION_MODE_PASSPHRASE
    assert info["data"][CONF_USER_ID].startswith(ENC_PREFIX_PASSPHRASE)
    assert CONF_USE_ESCROW_PASSPHRASE not in info["data"]
    assert CONF_ENCRYPTION_PASSPHRASE not in info["data"]
    assert info["passphrase"] == phrase


def _make_encrypted_entry(
    hass: _FakeHass,
    entry_id: str,
    user_id: str,
    *,
    passphrase: str | None = None,
) -> _FakeEntry:
    """Create a v2 encrypted ConfigEntry in passphrase or keyfile mode."""
    agency = "710100"
    host = "https://test.bib.dk"
    if passphrase:
        data = {
            CONF_MUNICIPALITY: "Test Kommune",
            CONF_HOST: host,
            CONF_AGENCY: agency,
            CONF_USER_ID: encrypt_with_passphrase(
                passphrase, user_id, field=CONF_USER_ID, agency=agency
            ),
            CONF_PINCODE: encrypt_with_passphrase(
                passphrase, "4321", field=CONF_PINCODE, agency=agency
            ),
            CONF_ENCRYPTION_MODE: ENCRYPTION_MODE_PASSPHRASE,
        }
    else:
        master = get_or_create_master_key(hass)
        data = {
            CONF_MUNICIPALITY: "Test Kommune",
            CONF_HOST: host,
            CONF_AGENCY: agency,
            CONF_USER_ID: encrypt_with_key(
                master, user_id, field=CONF_USER_ID, agency=agency
            ),
            CONF_PINCODE: encrypt_with_key(
                master, "4321", field=CONF_PINCODE, agency=agency
            ),
            CONF_ENCRYPTION_MODE: ENCRYPTION_MODE_KEYFILE,
        }
    return _FakeEntry(data=data, entry_id=entry_id, unique_id=f"uid-{entry_id}")


@pytest.mark.asyncio
async def test_unlock_wrong_passphrase_does_not_overwrite_escrow_when_entries_loaded(
    tmp_path: Path,
):
    from homeassistant.core import ServiceCall

    hass = _FakeHass(tmp_path)
    assert await async_setup(hass, {}) is True
    unlock_handler = hass.services.registered[(DOMAIN, SERVICE_UNLOCK)]

    phrase = _secret("family-escrow-loaded")
    wrong_phrase = _secret("wrong-loaded")
    e1 = _make_encrypted_entry(hass, "e1", "0102991234", passphrase=phrase)
    e2 = _make_encrypted_entry(hass, "e2", "0203005678", passphrase=phrase)
    hass.config_entries.entries.extend([e1, e2])
    hass.data.setdefault(DOMAIN, {})["e1"] = object()
    hass.data[DOMAIN]["e2"] = object()
    hass.data[DATA_ESCROW_PASSPHRASE] = phrase
    hass.data[DATA_PASSPHRASES] = {"e1": phrase, "e2": phrase}

    res = await unlock_handler(ServiceCall({CONF_ENCRYPTION_PASSPHRASE: wrong_phrase}))
    assert res == {
        "escrow_active": True,
        "unlocked": [],
        "already_unlocked": [],
        "failed": ["e1", "e2"],
    }
    assert hass.data[DATA_ESCROW_PASSPHRASE] == phrase
    assert hass.data[DATA_PASSPHRASES]["e1"] == phrase
    assert hass.data[DATA_PASSPHRASES]["e2"] == phrase

    # Entry unlocked via Reauth UI (no escrow set) must not activate wrong escrow.
    hass.data.pop(DATA_ESCROW_PASSPHRASE)
    res_no_escrow = await unlock_handler(
        ServiceCall({CONF_ENCRYPTION_PASSPHRASE: wrong_phrase})
    )
    assert res_no_escrow == {
        "escrow_active": False,
        "unlocked": [],
        "already_unlocked": [],
        "failed": ["e1", "e2"],
    }
    assert DATA_ESCROW_PASSPHRASE not in hass.data

    # Correct passphrase verifies loaded entries and restores escrow.
    res_ok = await unlock_handler(ServiceCall({CONF_ENCRYPTION_PASSPHRASE: phrase}))
    assert res_ok == {
        "escrow_active": True,
        "unlocked": [],
        "already_unlocked": ["e1", "e2"],
        "failed": [],
    }
    assert hass.data[DATA_ESCROW_PASSPHRASE] == phrase


@pytest.mark.asyncio
async def test_unlock_wrong_passphrase_with_keyfile_and_locked_entry_does_not_set_escrow(
    tmp_path: Path,
):
    from homeassistant.core import ServiceCall

    hass = _FakeHass(tmp_path)
    assert await async_setup(hass, {}) is True
    unlock_handler = hass.services.registered[(DOMAIN, SERVICE_UNLOCK)]

    phrase = _secret("valid-locked")
    wrong_phrase = _secret("wrong-mixed")
    k1 = _make_encrypted_entry(hass, "k1", "0102991234", passphrase=None)
    p1 = _make_encrypted_entry(hass, "p1", "0203005678", passphrase=phrase)
    hass.config_entries.entries.extend([k1, p1])

    res = await unlock_handler(ServiceCall({CONF_ENCRYPTION_PASSPHRASE: wrong_phrase}))
    assert res == {
        "escrow_active": False,
        "unlocked": [],
        "already_unlocked": ["k1"],
        "failed": ["p1"],
    }
    assert DATA_ESCROW_PASSPHRASE not in hass.data


@pytest.mark.asyncio
async def test_unlock_initializes_escrow_with_zero_or_keyfile_only_entries(
    tmp_path: Path,
):
    from homeassistant.core import ServiceCall

    hass = _FakeHass(tmp_path)
    assert await async_setup(hass, {}) is True
    unlock_handler = hass.services.registered[(DOMAIN, SERVICE_UNLOCK)]

    phrase = _secret("init-empty")
    res_empty = await unlock_handler(ServiceCall({CONF_ENCRYPTION_PASSPHRASE: phrase}))
    assert res_empty == {
        "escrow_active": True,
        "unlocked": [],
        "already_unlocked": [],
        "failed": [],
    }
    assert hass.data[DATA_ESCROW_PASSPHRASE] == phrase

    hass.data.pop(DATA_ESCROW_PASSPHRASE)
    k1 = _make_encrypted_entry(hass, "k1", "0102991234", passphrase=None)
    hass.config_entries.entries.append(k1)

    phrase2 = _secret("init-keyfile-only")
    res_keyfile = await unlock_handler(
        ServiceCall({CONF_ENCRYPTION_PASSPHRASE: phrase2})
    )
    assert res_keyfile == {
        "escrow_active": True,
        "unlocked": [],
        "already_unlocked": ["k1"],
        "failed": [],
    }
    assert hass.data[DATA_ESCROW_PASSPHRASE] == phrase2

    # Targeted unlock on a keyfile entry does not replace existing global escrow.
    other_phrase = _secret("targeted-keyfile-ignored")
    res_targeted = await unlock_handler(
        ServiceCall(
            {
                CONF_ENCRYPTION_PASSPHRASE: other_phrase,
                CONF_CONFIG_ENTRY_ID: "k1",
            }
        )
    )
    assert res_targeted == {
        "escrow_active": True,
        "unlocked": [],
        "already_unlocked": ["k1"],
        "failed": [],
    }
    assert hass.data[DATA_ESCROW_PASSPHRASE] == phrase2


@pytest.mark.asyncio
async def test_unlock_targeted_entry_does_not_overwrite_existing_global_escrow(
    tmp_path: Path,
):
    from homeassistant.core import ServiceCall

    hass = _FakeHass(tmp_path)
    assert await async_setup(hass, {}) is True
    unlock_handler = hass.services.registered[(DOMAIN, SERVICE_UNLOCK)]

    family_phrase = _secret("family-global")
    personal_phrase = _secret("personal-targeted")
    p1 = _make_encrypted_entry(hass, "p1", "0102991234", passphrase=family_phrase)
    p2 = _make_encrypted_entry(hass, "p2", "0203005678", passphrase=personal_phrase)
    hass.config_entries.entries.extend([p1, p2])
    hass.data[DATA_ESCROW_PASSPHRASE] = family_phrase

    res = await unlock_handler(
        ServiceCall(
            {
                CONF_ENCRYPTION_PASSPHRASE: personal_phrase,
                CONF_CONFIG_ENTRY_ID: "p2",
            }
        )
    )
    assert res == {
        "escrow_active": True,
        "unlocked": ["p2"],
        "already_unlocked": [],
        "failed": [],
    }
    assert hass.config_entries.reloaded == ["p2"]
    assert hass.data[DATA_PASSPHRASES]["p2"] == personal_phrase
    assert hass.data[DATA_ESCROW_PASSPHRASE] == family_phrase
