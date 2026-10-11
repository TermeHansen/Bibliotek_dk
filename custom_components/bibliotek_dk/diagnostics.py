"""Diagnostics support for the Bibliotek integration.

Exposes the config entry and a snapshot of the fetched account state so users
can attach it to bug reports. Credentials (CPR/library number and PIN) are
redacted.
"""

from __future__ import annotations

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_NAME, CONF_PINCODE, CONF_USER_ID, DOMAIN

# Credentials plus the configured account name and the patron's real name, so a
# shared diagnostics file does not leak the user's identity.
TO_REDACT = {CONF_NAME, CONF_PINCODE, CONF_USER_ID, "name"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict:
    """Return diagnostics for a config entry."""
    library = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    user = library.user if library is not None else None

    data = {
        "entry": {**entry.data, **entry.options},
        "account": {
            "name": getattr(user, "name", None),
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
