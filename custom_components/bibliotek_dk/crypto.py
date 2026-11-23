"""Authenticated encryption, key management, and keyed unique IDs for Bibliotek."""

from __future__ import annotations

import base64
import contextlib
import errno
import hashlib
import hmac
import logging
import os
from pathlib import Path
import secrets
import threading

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .const import (
    CONF_ENCRYPTION_KEY,
    CONF_KEY_FILE,
    CONF_PINCODE,
    CONF_USER_ID,
    DATA_CONFIG,
    DATA_MASTER_KEY,
    DOMAIN,
    ENV_ENCRYPTION_KEY,
    KEY_FILE_NAME,
)

_LOGGER = logging.getLogger(__name__)

ENC_PREFIX_KEYFILE = "enc:v1:"
ENC_PREFIX_PASSPHRASE = "enc:p1:"  # noqa: S105

_NONCE_LEN = 12
_SALT_LEN = 16
_KEY_LEN = 32
_SCRYPT_N = 2**16
_SCRYPT_R = 8
_SCRYPT_P = 1
_EXT_MASTER_SALT = b"bibliotek_dk:ext-master:v1"
_UID_PASSPHRASE_SALT = b"bibliotek_dk:uid:p1"

_KEY_FILE_LOCKS_GUARD = threading.Lock()
_KEY_FILE_LOCKS: dict[Path, threading.Lock] = {}


def _key_file_lock(path: Path) -> threading.Lock:
    resolved = path.resolve(strict=False)
    with _KEY_FILE_LOCKS_GUARD:
        lock = _KEY_FILE_LOCKS.get(resolved)
        if lock is None:
            lock = threading.Lock()
            _KEY_FILE_LOCKS[resolved] = lock
        return lock


class CredentialDecryptionError(Exception):
    """Raised when stored credentials cannot be decrypted or authenticated."""


class PassphraseRequiredError(CredentialDecryptionError):
    """Raised when a passphrase-protected entry needs unlocking."""


def is_encrypted(value: object) -> bool:
    """Return True if a stored credential value uses an encrypted envelope."""
    return isinstance(value, str) and value.startswith(
        (ENC_PREFIX_KEYFILE, ENC_PREFIX_PASSPHRASE)
    )


def is_passphrase_encrypted(value: object) -> bool:
    """Return True if a stored credential uses passphrase-based encryption."""
    return isinstance(value, str) and value.startswith(ENC_PREFIX_PASSPHRASE)


def _derive_subkey(master_key: bytes, info: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=_KEY_LEN,
        salt=None,
        info=info,
    ).derive(master_key)


def _derive_scrypt_key(secret: str, salt: bytes) -> bytes:
    return Scrypt(
        salt=salt,
        length=_KEY_LEN,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
    ).derive(secret.encode("utf-8"))


def _aad(version: str, field: str, agency: str) -> bytes:
    return f"{DOMAIN}:{version}:{field}:{agency}".encode("utf-8")


def encrypt_with_key(
    master_key: bytes, plaintext: str, *, field: str, agency: str
) -> str:
    """Encrypt plaintext using HKDF-derived key + AES-256-GCM."""
    enc_key = _derive_subkey(master_key, b"bibliotek_dk:cred-enc:v1")
    nonce = os.urandom(_NONCE_LEN)
    ct = AESGCM(enc_key).encrypt(
        nonce, plaintext.encode("utf-8"), _aad("v1", field, agency)
    )
    return ENC_PREFIX_KEYFILE + base64.urlsafe_b64encode(nonce + ct).decode("ascii")


def decrypt_with_key(master_key: bytes, token: str, *, field: str, agency: str) -> str:
    """Decrypt an ``enc:v1:`` credential envelope."""
    if not token.startswith(ENC_PREFIX_KEYFILE):
        raise CredentialDecryptionError("Unexpected credential envelope prefix")
    try:
        raw = base64.urlsafe_b64decode(token[len(ENC_PREFIX_KEYFILE) :].encode("ascii"))
    except (ValueError, UnicodeEncodeError) as err:
        raise CredentialDecryptionError("Malformed encrypted credential") from err
    if len(raw) < _NONCE_LEN + 16:
        raise CredentialDecryptionError("Truncated encrypted credential")
    nonce, ct = raw[:_NONCE_LEN], raw[_NONCE_LEN:]
    enc_key = _derive_subkey(master_key, b"bibliotek_dk:cred-enc:v1")
    try:
        pt = AESGCM(enc_key).decrypt(nonce, ct, _aad("v1", field, agency))
        return pt.decode("utf-8")
    except (InvalidTag, UnicodeDecodeError) as err:
        raise CredentialDecryptionError(
            "Failed to decrypt credential with configured key"
        ) from err


def encrypt_with_passphrase(
    passphrase: str, plaintext: str, *, field: str, agency: str
) -> str:
    """Encrypt plaintext using Scrypt + AES-256-GCM with a user passphrase."""
    if not passphrase:
        raise PassphraseRequiredError("Encryption passphrase is required")
    salt = os.urandom(_SALT_LEN)
    nonce = os.urandom(_NONCE_LEN)
    key = _derive_scrypt_key(passphrase, salt)
    ct = AESGCM(key).encrypt(
        nonce, plaintext.encode("utf-8"), _aad("p1", field, agency)
    )
    payload = salt + nonce + ct
    return ENC_PREFIX_PASSPHRASE + base64.urlsafe_b64encode(payload).decode("ascii")


def decrypt_with_passphrase(
    passphrase: str | None, token: str, *, field: str, agency: str
) -> str:
    """Decrypt an ``enc:p1:`` passphrase-protected credential envelope."""
    if not passphrase:
        raise PassphraseRequiredError(
            "Encryption passphrase is required to unlock this account"
        )
    if not token.startswith(ENC_PREFIX_PASSPHRASE):
        raise CredentialDecryptionError("Unexpected passphrase envelope prefix")
    try:
        raw = base64.urlsafe_b64decode(
            token[len(ENC_PREFIX_PASSPHRASE) :].encode("ascii")
        )
    except (ValueError, UnicodeEncodeError) as err:
        raise CredentialDecryptionError("Malformed encrypted credential") from err
    if len(raw) < _SALT_LEN + _NONCE_LEN + 16:
        raise CredentialDecryptionError("Truncated encrypted credential")
    salt = raw[:_SALT_LEN]
    nonce = raw[_SALT_LEN : _SALT_LEN + _NONCE_LEN]
    ct = raw[_SALT_LEN + _NONCE_LEN :]
    key = _derive_scrypt_key(passphrase, salt)
    try:
        pt = AESGCM(key).decrypt(nonce, ct, _aad("p1", field, agency))
        return pt.decode("utf-8")
    except (InvalidTag, UnicodeDecodeError) as err:
        raise CredentialDecryptionError(
            "Failed to decrypt credential (wrong passphrase or corrupted data)"
        ) from err


def compute_keyed_unique_id(master_key: bytes, host: str, user_id: str) -> str:
    """Compute a keyed HMAC-SHA256 ConfigEntry unique_id (never exposes raw CPR)."""
    uid_key = _derive_subkey(master_key, b"bibliotek_dk:unique-id:v1")
    digest = hmac.new(
        uid_key, f"{host}:{user_id}".encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"hmac_v1:{digest}"


def compute_passphrase_unique_id(passphrase: str, host: str, user_id: str) -> str:
    """Compute a passphrase-keyed HMAC-SHA256 ConfigEntry unique_id."""
    if not passphrase:
        raise PassphraseRequiredError("Encryption passphrase is required")
    uid_key = _derive_scrypt_key(passphrase, _UID_PASSPHRASE_SALT)
    digest = hmac.new(
        uid_key, f"{host}:{user_id}".encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"hmac_p1:{digest}"


def _read_key_file(path: Path) -> bytes:
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        _LOGGER.warning(
            "Key file %s has permissive permissions (%04o); recommend 0600",
            path,
            mode,
        )
    try:
        raw_text = path.read_text(encoding="ascii").strip()
        key = base64.urlsafe_b64decode(raw_text.encode("ascii"))
    except (OSError, ValueError, UnicodeDecodeError) as err:
        raise CredentialDecryptionError(f"Failed to read key file {path}") from err
    if len(key) != _KEY_LEN:
        raise CredentialDecryptionError(f"Invalid key length in {path}")
    return key


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("Short write while writing key file")
        view = view[written:]


def load_or_create_key_file(path: Path) -> bytes:
    """Load a 32-byte master key from ``path`` or create one atomically (0600)."""
    with _key_file_lock(path):
        if path.exists():
            return _read_key_file(path)

        path.parent.mkdir(parents=True, exist_ok=True)
        key = os.urandom(_KEY_LEN)
        encoded = (base64.urlsafe_b64encode(key).decode("ascii") + "\n").encode("ascii")
        tmp_path = path.with_name(
            f".{path.name}.{os.getpid()}.{threading.get_ident()}.{secrets.token_hex(8)}.tmp"
        )
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            try:
                os.fchmod(fd, 0o600)
                _write_all(fd, encoded)
                os.fsync(fd)
            finally:
                os.close(fd)

            try:
                os.link(tmp_path, path)
            except FileExistsError:
                return _read_key_file(path)
            except OSError as err:
                if err.errno not in (
                    errno.EPERM,
                    errno.EXDEV,
                    errno.EOPNOTSUPP,
                    errno.ENOSYS,
                ):
                    raise
                try:
                    fd_dst = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    return _read_key_file(path)
                try:
                    os.fchmod(fd_dst, 0o600)
                    _write_all(fd_dst, encoded)
                    os.fsync(fd_dst)
                except Exception:
                    with contextlib.suppress(OSError):
                        path.unlink()
                    raise
                finally:
                    os.close(fd_dst)
        finally:
            with contextlib.suppress(OSError):
                tmp_path.unlink()

        return key


def get_or_create_master_key(hass) -> bytes:
    """Resolve the master encryption key in strict priority order.

    Priority:
      1. ``BIBLIOTEK_DK_ENCRYPTION_KEY`` environment variable
      2. ``configuration.yaml`` ``bibliotek_dk: encryption_key:``
      3. ``configuration.yaml`` ``bibliotek_dk: key_file:``
      4. Default ``<config>/.storage/bibliotek_dk.key``
    """
    env_secret = os.environ.get(ENV_ENCRYPTION_KEY, "").strip()
    yaml_cfg = hass.data.get(DATA_CONFIG, {})
    yaml_secret = str(yaml_cfg.get(CONF_ENCRYPTION_KEY, "") or "").strip()

    secret = env_secret or yaml_secret
    if secret:
        fingerprint = hashlib.sha256(secret.encode("utf-8")).digest()
        cached = hass.data.get(DATA_MASTER_KEY)
        if isinstance(cached, tuple) and len(cached) == 2 and cached[0] == fingerprint:
            return cached[1]
        derived = _derive_scrypt_key(secret, _EXT_MASTER_SALT)
        hass.data[DATA_MASTER_KEY] = (fingerprint, derived)
        return derived

    custom_key_file = yaml_cfg.get(CONF_KEY_FILE)
    if custom_key_file:
        return load_or_create_key_file(Path(custom_key_file))

    return load_or_create_key_file(Path(hass.config.path(".storage", KEY_FILE_NAME)))


def _decrypt_field(
    hass, value: str, *, field: str, agency: str, passphrase: str | None
) -> str:
    if is_passphrase_encrypted(value):
        return decrypt_with_passphrase(passphrase, value, field=field, agency=agency)
    if isinstance(value, str) and value.startswith(ENC_PREFIX_KEYFILE):
        master_key = get_or_create_master_key(hass)
        return decrypt_with_key(master_key, value, field=field, agency=agency)
    return value


def decrypt_entry_credentials(
    hass,
    entry_data: dict,
    agency: str,
    passphrase: str | None = None,
) -> tuple[str, str]:
    """Decrypt ``user_id`` and ``pincode`` from config entry data."""
    user_id = _decrypt_field(
        hass,
        entry_data[CONF_USER_ID],
        field=CONF_USER_ID,
        agency=agency,
        passphrase=passphrase,
    )
    pincode = _decrypt_field(
        hass,
        entry_data[CONF_PINCODE],
        field=CONF_PINCODE,
        agency=agency,
        passphrase=passphrase,
    )
    return user_id, pincode
