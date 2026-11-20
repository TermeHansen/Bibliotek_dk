"""The Bibliotek integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .const import (
    ATTR_LOAN_IDS,
    CONF_AGENCY,
    CONF_CONFIG_ENTRY_ID,
    CONF_ENCRYPTION_KEY,
    CONF_ENCRYPTION_MODE,
    CONF_EXPOSE_PERSONAL_INFO,
    CONF_HOST,
    CONF_KEY_FILE,
    CONF_MUNICIPALITY,
    CONF_PINCODE,
    CONF_SHOW_ELOANS,
    CONF_USER_ID,
    DATA_CONFIG,
    DATA_PASSPHRASES,
    DOMAIN,
    ENCRYPTION_MODE_KEYFILE,
    OPTION_KEYS,
    SERVICE_RENEW_LOANS,
)
from .crypto import (
    CredentialDecryptionError,
    PassphraseRequiredError,
    compute_keyed_unique_id,
    decrypt_entry_credentials,
    decrypt_with_key,
    encrypt_with_key,
    get_or_create_master_key,
    is_encrypted,
)
from .library_api import Library
from .sensor import _legacy_md5_unique_id

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]

_LEGACY_ENTITY_SUFFIXES = (
    ("Loans_", "loans"),
    ("LoansOverdue_", "loans_overdue"),
    ("Debts_", "debts"),
    ("Reservations_", "reservations"),
    ("ReservationsReady_", "reservations_ready"),
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_ENCRYPTION_KEY): str,
                vol.Optional(CONF_KEY_FILE): str,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Store optional YAML encryption configuration."""
    if DOMAIN in config:
        hass.data[DATA_CONFIG] = dict(config[DOMAIN])
    return True


def _is_hex_md5(value: str | None) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 32
        and all(ch in "0123456789abcdef" for ch in value)
    )


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate v1 plaintext config entries to v2 encrypted credentials."""
    if entry.version > 2:
        return False

    if entry.version == 1:
        data = dict(entry.data)
        agency = str(data.get(CONF_AGENCY, ""))
        host = str(data.get(CONF_HOST, ""))

        def _migrate_sync() -> tuple[dict, str, str]:
            master_key = get_or_create_master_key(hass)
            raw_uid = str(data.get(CONF_USER_ID, ""))
            raw_pin = str(data.get(CONF_PINCODE, ""))
            plain_uid = (
                decrypt_with_key(master_key, raw_uid, field=CONF_USER_ID, agency=agency)
                if is_encrypted(raw_uid)
                else raw_uid
            )
            if not is_encrypted(raw_uid):
                data[CONF_USER_ID] = encrypt_with_key(
                    master_key, raw_uid, field=CONF_USER_ID, agency=agency
                )
            if not is_encrypted(raw_pin):
                data[CONF_PINCODE] = encrypt_with_key(
                    master_key, raw_pin, field=CONF_PINCODE, agency=agency
                )
            data[CONF_ENCRYPTION_MODE] = ENCRYPTION_MODE_KEYFILE
            data.setdefault(CONF_EXPOSE_PERSONAL_INFO, False)
            new_unique_id = compute_keyed_unique_id(master_key, host, plain_uid)
            return data, new_unique_id, plain_uid

        try:
            migrated_data, new_unique_id, plain_uid = await hass.async_add_executor_job(
                _migrate_sync
            )
        except CredentialDecryptionError:
            _LOGGER.exception(
                "Failed to migrate Bibliotek config entry %s to encrypted storage",
                entry.entry_id,
            )
            return False

        clean_options = {
            key: entry.options[key] for key in OPTION_KEYS if key in entry.options
        }

        registry = er.async_get(hass)
        legacy_map = {
            _legacy_md5_unique_id(prefix + plain_uid): f"{entry.entry_id}_{suffix}"
            for prefix, suffix in _LEGACY_ENTITY_SUFFIXES
        }
        for entity_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity_entry.unique_id.startswith(f"{entry.entry_id}_"):
                continue
            if entity_entry.unique_id in legacy_map:
                registry.async_update_entity(
                    entity_entry.entity_id,
                    new_unique_id=legacy_map[entity_entry.unique_id],
                )
            elif _is_hex_md5(entity_entry.unique_id):
                registry.async_update_entity(
                    entity_entry.entity_id,
                    new_unique_id=f"{entry.entry_id}_main",
                )

        hass.config_entries.async_update_entry(
            entry,
            data=migrated_data,
            options=clean_options,
            unique_id=new_unique_id,
            version=2,
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Bibliotek from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    passphrases = hass.data.setdefault(DATA_PASSPHRASES, {})
    passphrase = passphrases.get(entry.entry_id)
    unique_id = getattr(entry, "unique_id", None)
    if passphrase is None and unique_id and unique_id in passphrases:
        passphrase = passphrases.pop(unique_id)
        passphrases[entry.entry_id] = passphrase

    agency = str(entry.data.get(CONF_AGENCY, ""))
    try:
        user_id, pincode = await hass.async_add_executor_job(
            decrypt_entry_credentials,
            hass,
            entry.data,
            agency,
            passphrase,
        )
    except (PassphraseRequiredError, CredentialDecryptionError) as err:
        raise ConfigEntryAuthFailed(str(err)) from err

    municipality = entry.data.get(CONF_MUNICIPALITY, "")
    account_label = f"{municipality or 'library'}:{entry.entry_id[:8]}"
    new_data = {**entry.data, **entry.options}
    hass.data[DOMAIN][entry.entry_id] = Library(
        user_id,
        pincode,
        entry.data[CONF_HOST],
        agency,
        libraryName=municipality,
        use_eReolen=new_data.get(CONF_SHOW_ELOANS, True),
        account_label=account_label,
    )
    # update options listener
    entry.async_on_unload(entry.add_update_listener(update_listener))

    # The service is global (one handler for all entries); register it once.
    # supports_response lets automations read the per-loan status back.
    if not hass.services.has_service(DOMAIN, SERVICE_RENEW_LOANS):
        hass.services.async_register(
            DOMAIN,
            SERVICE_RENEW_LOANS,
            _make_renew_loans_handler(hass),
            supports_response=SupportsResponse.OPTIONAL,
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


def _make_renew_loans_handler(hass: HomeAssistant):
    """Build the ``renew_loans`` service handler.

    Renews the given physical loans and asks the coordinator for a refresh so
    the new due dates show up immediately instead of at the next poll.
    """

    async def async_renew_loans(call: ServiceCall) -> dict:
        entry_id = call.data[CONF_CONFIG_ENTRY_ID]
        library = hass.data.get(DOMAIN, {}).get(entry_id)
        if library is None:
            raise HomeAssistantError(
                f"No Bibliotek configuration entry with id '{entry_id}'"
            )
        loan_ids = _normalize_loan_ids(call.data.get(ATTR_LOAN_IDS))

        status = await hass.async_add_executor_job(library.renewLoans, loan_ids)
        if any(result == "renewed" for result in status.values()):
            # Refresh the sensors right away (the coordinator lives in sensor.py)
            # instead of reloading the entry, which would rebuild the Library and
            # discard the last-good results kept for API outages.
            coordinator = getattr(library, "coordinator", None)
            if coordinator is not None:
                await coordinator.async_request_refresh()
        else:
            # Surface a hard failure (bad ids, blocked patron, ...) to the caller
            # instead of silently reporting success.
            raise HomeAssistantError(
                f"None of the loans could be renewed: {status or 'request failed'}"
            )
        return {"renewed": status}

    return async_renew_loans


def _normalize_loan_ids(raw) -> list[int]:
    """Return the loan ids as a list of ints, or raise a clear error."""
    if raw is None:
        raise HomeAssistantError("No loan ids given to renew")
    if isinstance(raw, (str, int)):
        raw = [raw]
    loan_ids = []
    for value in raw:
        try:
            loan_ids.append(int(value))
        except (TypeError, ValueError) as err:
            raise HomeAssistantError(
                f"Invalid loan id '{value}': loan ids must be integers"
            ) from err
    if not loan_ids:
        raise HomeAssistantError("No loan ids given to renew")
    return loan_ids


async def update_listener(hass, entry):
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry (retains RAM passphrases across options reloads)."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        library = hass.data[DOMAIN].pop(entry.entry_id)
        await hass.async_add_executor_job(library.session.close)
        # Drop the service once the last entry is gone.
        if not hass.data.get(DOMAIN):
            hass.services.async_remove(DOMAIN, SERVICE_RENEW_LOANS)

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Purge any in-memory passphrase when a config entry is deleted."""
    hass.data.get(DATA_PASSPHRASES, {}).pop(entry.entry_id, None)
