"""Diagnostics support for the Bibliotek integration.

Exposes the config entry and a snapshot of the fetched account state so users
can attach it to bug reports. Credentials (CPR/library number and PIN) are
redacted.
"""

from __future__ import annotations

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ENCRYPTION_MODE,
    CONF_ENCRYPTION_PASSPHRASE,
    CONF_NAME,
    CONF_PINCODE,
    CONF_USER_ID,
    DOMAIN,
)

# Credentials plus personal identity/contact attributes, so a shared diagnostics
# file never leaks credentials or patron PII.
TO_REDACT = {
    CONF_NAME,
    CONF_PINCODE,
    CONF_USER_ID,
    CONF_ENCRYPTION_PASSPHRASE,
    "name",
    "address",
    "phone",
    "mail",
    "unique_id",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict:
    """Return diagnostics for a config entry."""
    library = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    user = library.user if library is not None else None

    data = {
        "encryption_mode": entry.data.get(CONF_ENCRYPTION_MODE, "plaintext"),
        "entry": {
            "unique_id": getattr(entry, "unique_id", None),
            **entry.data,
            **entry.options,
        },
        "account": {
            "name": getattr(user, "name", None),
            "address": getattr(user, "address", None),
            "phone": getattr(user, "phone", None),
            "mail": getattr(user, "mail", None),
            "pickup_library": getattr(user, "pickupLibrary", None),
            "counts": {
                "loans": len(getattr(user, "loans", []) or []),
                "loans_overdue": len(getattr(user, "loansOverdue", []) or []),
                "reservations": len(getattr(user, "reservations", []) or []),
                "reservations_ready": len(getattr(user, "reservationsReady", []) or []),
                "debts": len(getattr(user, "debts", []) or []),
            },
            "debts_amount": getattr(user, "debtsAmount", None),
        },
    }
    return async_redact_data(data, TO_REDACT)
