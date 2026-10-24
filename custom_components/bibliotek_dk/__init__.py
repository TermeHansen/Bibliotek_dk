"""The Bibliotek integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError

from .library_api import Library

from .const import (
    ATTR_LOAN_IDS,
    CONF_AGENCY,
    CONF_CONFIG_ENTRY_ID,
    CONF_HOST,
    CONF_MUNICIPALITY,
    CONF_PINCODE,
    CONF_SHOW_ELOANS,
    CONF_USER_ID,
    DOMAIN,
    SERVICE_RENEW_LOANS,
)

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Bibliotek from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    new_data = {**entry.data, **entry.options}
    hass.data[DOMAIN][entry.entry_id] = Library(
        entry.data[CONF_USER_ID],
        entry.data[CONF_PINCODE],
        entry.data[CONF_HOST],
        entry.data[CONF_AGENCY],
        libraryName=entry.data[CONF_MUNICIPALITY],
        use_eReolen=new_data.get(CONF_SHOW_ELOANS, True),
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
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        library = hass.data[DOMAIN].pop(entry.entry_id)
        await hass.async_add_executor_job(library.session.close)
        # Drop the service once the last entry is gone.
        if not hass.data.get(DOMAIN):
            hass.services.async_remove(DOMAIN, SERVICE_RENEW_LOANS)

    return unload_ok
