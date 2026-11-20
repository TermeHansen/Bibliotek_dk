"""Unit tests for credential encryption, key resolution, and keyed unique IDs."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import stat

import pytest

from custom_components.bibliotek_dk.const import (
    CONF_ENCRYPTION_KEY,
    CONF_KEY_FILE,
    CONF_PINCODE,
    CONF_USER_ID,
    DATA_CONFIG,
    ENV_ENCRYPTION_KEY,
    KEY_FILE_NAME,
)
from custom_components.bibliotek_dk.crypto import (
    ENC_PREFIX_KEYFILE,
    ENC_PREFIX_PASSPHRASE,
    CredentialDecryptionError,
    PassphraseRequiredError,
    compute_keyed_unique_id,
    compute_passphrase_unique_id,
    decrypt_entry_credentials,
    decrypt_with_key,
    decrypt_with_passphrase,
    encrypt_with_key,
    encrypt_with_passphrase,
    get_or_create_master_key,
    is_encrypted,
    is_passphrase_encrypted,
    load_or_create_key_file,
)


def _secret(label: str) -> str:
    """Build a synthetic secret string without triggering Ruff S105/S106."""
    return f"test-{label}-material-99"


class _FakeConfig:
    def __init__(self, root: Path) -> None:
        self._root = root

    def path(self, *parts: str) -> str:
        return str(self._root.joinpath(*parts))


class _FakeHass:
    def __init__(self, root: Path, yaml_cfg: dict | None = None) -> None:
        self.config = _FakeConfig(root)
        self.data = {}
        if yaml_cfg is not None:
            self.data[DATA_CONFIG] = yaml_cfg


def test_keyfile_encryption_roundtrip_unique_nonces_and_aad():
    master = bytes(range(32))
    other_master = bytes(reversed(range(32)))
    cpr = "0102991234"

    ct1 = encrypt_with_key(master, cpr, field=CONF_USER_ID, agency="710100")
    ct2 = encrypt_with_key(master, cpr, field=CONF_USER_ID, agency="710100")

    assert ct1.startswith(ENC_PREFIX_KEYFILE)
    assert ct2.startswith(ENC_PREFIX_KEYFILE)
    assert ct1 != ct2
    assert cpr not in ct1
    assert "010299" not in ct1
    assert is_encrypted(ct1) is True
    assert is_passphrase_encrypted(ct1) is False

    assert decrypt_with_key(master, ct1, field=CONF_USER_ID, agency="710100") == cpr

    with pytest.raises(CredentialDecryptionError):
        decrypt_with_key(other_master, ct1, field=CONF_USER_ID, agency="710100")

    # AAD binds ciphertext to field and agency.
    with pytest.raises(CredentialDecryptionError):
        decrypt_with_key(master, ct1, field=CONF_PINCODE, agency="710100")
    with pytest.raises(CredentialDecryptionError):
        decrypt_with_key(master, ct1, field=CONF_USER_ID, agency="775100")

    with pytest.raises(CredentialDecryptionError):
        decrypt_with_key(
            master, ENC_PREFIX_KEYFILE + "bad", field=CONF_USER_ID, agency="710100"
        )


def test_passphrase_encryption_roundtrip_and_aad():
    phrase = _secret("pass")
    wrong_phrase = _secret("wrong")
    cpr = "0102991234"

    ct1 = encrypt_with_passphrase(phrase, cpr, field=CONF_USER_ID, agency="710100")
    ct2 = encrypt_with_passphrase(phrase, cpr, field=CONF_USER_ID, agency="710100")

    assert ct1.startswith(ENC_PREFIX_PASSPHRASE)
    assert ct1 != ct2
    assert cpr not in ct1
    assert is_encrypted(ct1) is True
    assert is_passphrase_encrypted(ct1) is True

    assert (
        decrypt_with_passphrase(phrase, ct1, field=CONF_USER_ID, agency="710100") == cpr
    )

    with pytest.raises(PassphraseRequiredError):
        decrypt_with_passphrase(None, ct1, field=CONF_USER_ID, agency="710100")
    with pytest.raises(PassphraseRequiredError):
        decrypt_with_passphrase("", ct1, field=CONF_USER_ID, agency="710100")

    with pytest.raises(CredentialDecryptionError):
        decrypt_with_passphrase(wrong_phrase, ct1, field=CONF_USER_ID, agency="710100")

    with pytest.raises(CredentialDecryptionError):
        decrypt_with_passphrase(phrase, ct1, field=CONF_PINCODE, agency="710100")


def test_key_file_created_with_0600_and_persists(tmp_path: Path, caplog):
    key_path = tmp_path / ".storage" / KEY_FILE_NAME
    key1 = load_or_create_key_file(key_path)
    assert len(key1) == 32
    assert key_path.exists()
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600

    key2 = load_or_create_key_file(key_path)
    assert key1 == key2

    # Permissive permissions trigger a warning when loading.
    os.chmod(key_path, 0o644)
    with caplog.at_level(logging.WARNING):
        key3 = load_or_create_key_file(key_path)
    assert key3 == key1
    assert any(
        "0600" in rec.getMessage() or "permissive" in rec.getMessage().lower()
        for rec in caplog.records
    )


def test_master_key_resolution_priority(tmp_path: Path, monkeypatch):
    yaml_key_path = tmp_path / "custom.key"
    hass = _FakeHass(
        tmp_path,
        yaml_cfg={
            CONF_ENCRYPTION_KEY: _secret("yaml"),
            CONF_KEY_FILE: str(yaml_key_path),
        },
    )

    monkeypatch.setenv(ENV_ENCRYPTION_KEY, _secret("env"))
    env_key = get_or_create_master_key(hass)
    assert len(env_key) == 32
    assert not yaml_key_path.exists()

    monkeypatch.delenv(ENV_ENCRYPTION_KEY, raising=False)
    yaml_key = get_or_create_master_key(hass)
    assert len(yaml_key) == 32
    assert yaml_key != env_key
    assert not yaml_key_path.exists()

    hass.data[DATA_CONFIG].pop(CONF_ENCRYPTION_KEY)
    file_key = get_or_create_master_key(hass)
    assert len(file_key) == 32
    assert yaml_key_path.exists()
    assert file_key == load_or_create_key_file(yaml_key_path)

    hass.data.pop(DATA_CONFIG)
    default_key = get_or_create_master_key(hass)
    assert (tmp_path / ".storage" / KEY_FILE_NAME).exists()
    assert default_key != file_key


def test_keyed_unique_ids_never_expose_raw_cpr():
    master = bytes(range(32))
    cpr = "0102991234"
    uid1 = compute_keyed_unique_id(master, "https://bib1.dk", cpr)
    uid2 = compute_keyed_unique_id(master, "https://bib1.dk", cpr)
    uid3 = compute_keyed_unique_id(master, "https://bib2.dk", cpr)

    assert uid1 == uid2
    assert uid1 != uid3
    assert uid1.startswith("hmac_v1:")
    assert cpr not in uid1
    assert "010299" not in uid1

    phrase = _secret("uid")
    p_uid1 = compute_passphrase_unique_id(phrase, "https://bib1.dk", cpr)
    p_uid2 = compute_passphrase_unique_id(phrase, "https://bib1.dk", cpr)
    assert p_uid1 == p_uid2
    assert p_uid1.startswith("hmac_p1:")
    assert cpr not in p_uid1


def test_decrypt_entry_credentials_handles_all_modes(tmp_path: Path):
    hass = _FakeHass(tmp_path)
    master = get_or_create_master_key(hass)
    cpr = "0102991234"
    pin = "4321"

    keyfile_data = {
        CONF_USER_ID: encrypt_with_key(
            master, cpr, field=CONF_USER_ID, agency="710100"
        ),
        CONF_PINCODE: encrypt_with_key(
            master, pin, field=CONF_PINCODE, agency="710100"
        ),
    }
    assert decrypt_entry_credentials(hass, keyfile_data, "710100") == (cpr, pin)

    phrase = _secret("entry")
    pass_data = {
        CONF_USER_ID: encrypt_with_passphrase(
            phrase, cpr, field=CONF_USER_ID, agency="710100"
        ),
        CONF_PINCODE: encrypt_with_passphrase(
            phrase, pin, field=CONF_PINCODE, agency="710100"
        ),
    }
    assert decrypt_entry_credentials(hass, pass_data, "710100", phrase) == (cpr, pin)

    with pytest.raises(PassphraseRequiredError):
        decrypt_entry_credentials(hass, pass_data, "710100", None)

    legacy_data = {CONF_USER_ID: cpr, CONF_PINCODE: pin}
    assert decrypt_entry_credentials(hass, legacy_data, "710100") == (cpr, pin)
