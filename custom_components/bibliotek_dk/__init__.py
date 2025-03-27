"""The Dummy Garage integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .library_api import Library

from .const import (
    CONF_AGENCY,
    CONF_HOST,
    CONF_MUNICIPALITY,
    CONF_PINCODE,
    CONF_USER_ID,
    CONF_SHOW_ELOANS,
    DOMAIN,
)

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:

    """Set up Bibliotek from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    hass.data[DOMAIN][entry.entry_id] = Library(
        entry.data[CONF_USER_ID],
        entry.data[CONF_PINCODE],
        entry.data[CONF_HOST],
        entry.data[CONF_AGENCY],
        libraryName=entry.data[CONF_MUNICIPALITY],
        use_eReolen=entry.data.get(CONF_SHOW_ELOANS, True),
    )
    # update options listener
    entry.async_on_unload(entry.add_update_listener(update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def update_listener(hass, entry):
    """Handle options update."""
    myLibrary = hass.data[DOMAIN][entry.entry_id]
    myLibrary.use_eReolen = entry.options[CONF_SHOW_ELOANS]
    await hass.async_add_executor_job(myLibrary.update)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
