"""Config flow for Bibliotek integration."""

from __future__ import annotations

from collections.abc import Mapping
import hmac
import json
import logging
import re
from typing import Any

from bs4 import BeautifulSoup as BS
import requests
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector

from .const import (
    CONF_AGENCY,
    CONF_BRANCH_ID,
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
    HEADERS,
    MUNICIPALITY_LOOKUP_URL,
    OPTION_KEYS,
    UPDATE_INTERVAL,
    URL_FALLBACK,
    URL_LOGIN_PAGE,
)
from .crypto import (
    CredentialDecryptionError,
    PassphraseRequiredError,
    compute_keyed_unique_id,
    compute_passphrase_unique_id,
    decrypt_entry_credentials,
    decrypt_with_passphrase,
    encrypt_with_key,
    encrypt_with_passphrase,
    get_or_create_master_key,
    is_passphrase_encrypted,
)
from .library_api import Library

_LOGGER = logging.getLogger(__name__)

# Timeout (seconds) applied to the library-list and municipality lookups.
REQUEST_TIMEOUT = 10

_SECRET_TEXT_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(
        type=selector.TextSelectorType.PASSWORD,
        autocomplete="off",
    )
)
_PASSPHRASE_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(
        type=selector.TextSelectorType.PASSWORD,
        autocomplete="new-password",
    )
)


def refreshLibraries() -> tuple[dict, dict]:
    """Fetch and parse the list of libraries from the fallback login page.

    Returns a ``(libraries, excLibraries)`` pair: libraries usable through this
    integration, and those that use ``gatewayf`` and therefore cannot be used.
    """
    session = requests.Session()
    session.headers = HEADERS

    try:
        r = session.get(URL_FALLBACK + URL_LOGIN_PAGE, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()

    except requests.exceptions.HTTPError as err:
        raise CannotConnect from err
    except requests.exceptions.Timeout as err:
        _LOGGER.error(f"Timeout fecthing ({URL_FALLBACK + URL_LOGIN_PAGE})")
        raise CannotConnect from err
    except requests.exceptions.TooManyRedirects as err:
        _LOGGER.error(f"Too many redirects fecthing ({URL_FALLBACK + URL_LOGIN_PAGE})")
        raise CannotConnect from err
    except requests.exceptions.RequestException as err:
        raise CannotConnect from err

    try:
        soup = BS(r.text, "html.parser")
        librariesJSON = json.loads(
            soup.find(
                "script",
                string=re.compile(r"^var libraries = (.)", re.MULTILINE | re.DOTALL),
            ).string.replace("var libraries = ", "")
        )
    except (AttributeError, KeyError) as err:
        _LOGGER.error(
            f"Error loading the libraries from the fallback url. Error: {err}"
        )
        return {}, {}

    libraries, excLibraries = {}, {}
    if "folk" in librariesJSON.keys():
        for library in librariesJSON["folk"]:
            p = re.compile(r"^.+?[^\/:](?=[?\/]|$)")
            m = p.match(library["registrationUrl"])
            # Only use libraries NOT using gatewayf
            if "gatewayf" not in library["registrationUrl"]:
                libraries[library[CONF_NAME]] = {
                    CONF_AGENCY: library[CONF_BRANCH_ID],
                    CONF_HOST: m.group(),
                }
            else:
                excLibraries[library[CONF_NAME]] = {
                    CONF_AGENCY: library[CONF_BRANCH_ID],
                    CONF_HOST: m.group(),
                }
    return libraries, excLibraries


def municipalityFromCoor(lon, lat) -> str:
    """Reverse-lookup the municipality name from a (lon, lat) coordinate."""
    session = requests.Session()
    session.headers = HEADERS

    try:
        r = session.get(
            MUNICIPALITY_LOOKUP_URL.replace("LON", str(lon)).replace("LAT", str(lat)),
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()

    except requests.exceptions.HTTPError as err:
        _LOGGER.error(f"HTTP Error while fetching municipality: {err}")
        return ""
    except requests.exceptions.Timeout as err:
        _LOGGER.error(f"Timeout fecthing municipality: {err}")
        return ""
    except requests.exceptions.TooManyRedirects as err:
        _LOGGER.error(f"Too many redirects fecthing municipality: {err}")
        return ""
    except requests.exceptions.RequestException as err:
        _LOGGER.error(f"Request Exception while fetching municipality: {err}")
        return ""

    municipality = json.loads(r.text)
    return municipality["navn"] if "navn" in municipality else ""


def _encrypt_credentials_sync(
    hass: HomeAssistant,
    plain_uid: str,
    plain_pin: str,
    host: str,
    agency: str,
    passphrase: str,
) -> tuple[str, str, str, str]:
    if passphrase:
        enc_uid = encrypt_with_passphrase(
            passphrase, plain_uid, field=CONF_USER_ID, agency=agency
        )
        enc_pin = encrypt_with_passphrase(
            passphrase, plain_pin, field=CONF_PINCODE, agency=agency
        )
        uid_hash = compute_passphrase_unique_id(passphrase, host, plain_uid)
        return enc_uid, enc_pin, ENCRYPTION_MODE_PASSPHRASE, uid_hash

    master_key = get_or_create_master_key(hass)
    enc_uid = encrypt_with_key(master_key, plain_uid, field=CONF_USER_ID, agency=agency)
    enc_pin = encrypt_with_key(master_key, plain_pin, field=CONF_PINCODE, agency=agency)
    uid_hash = compute_keyed_unique_id(master_key, host, plain_uid)
    return enc_uid, enc_pin, ENCRYPTION_MODE_KEYFILE, uid_hash


def _check_existing_entries_sync(
    hass: HomeAssistant,
    plain_uid: str,
    host: str,
    passphrase: str,
    exclude_entry_id: str | None,
) -> None:
    """Check loaded and unloaded config entries for duplicate accounts on ``host``."""
    loaded_ids: set[str] = set()
    for entry_id, library_obj in hass.data.get(DOMAIN, {}).items():
        loaded_ids.add(entry_id)
        if exclude_entry_id and entry_id == exclude_entry_id:
            continue
        if library_obj.host == host and hmac.compare_digest(
            library_obj.user.userId.encode("utf-8"),
            plain_uid.encode("utf-8"),
        ):
            raise UserExist

    config_entries_mgr = getattr(hass, "config_entries", None)
    if config_entries_mgr is None or not hasattr(config_entries_mgr, "async_entries"):
        return

    stored_passphrases = hass.data.get(DATA_PASSPHRASES, {})
    escrow_passphrase = str(hass.data.get(DATA_ESCROW_PASSPHRASE, "") or "")
    has_locked_same_host = False

    for entry in config_entries_mgr.async_entries(DOMAIN):
        entry_id = getattr(entry, "entry_id", None)
        if entry_id in loaded_ids or (
            exclude_entry_id and entry_id == exclude_entry_id
        ):
            continue
        entry_data = getattr(entry, "data", None) or {}
        if entry_data.get(CONF_HOST) != host:
            continue
        entry_uid_raw = entry_data.get(CONF_USER_ID)
        if not entry_uid_raw:
            continue
        entry_agency = str(entry_data.get(CONF_AGENCY, ""))
        entry_is_pass = entry_data.get(
            CONF_ENCRYPTION_MODE
        ) == ENCRYPTION_MODE_PASSPHRASE or is_passphrase_encrypted(entry_uid_raw)

        if entry_is_pass:
            candidates: list[str] = []
            for cand in (
                stored_passphrases.get(entry_id),
                escrow_passphrase,
                passphrase,
            ):
                if cand and cand not in candidates:
                    candidates.append(cand)
            existing_uid: str | None = None
            for cand in candidates:
                try:
                    existing_uid = decrypt_with_passphrase(
                        cand,
                        entry_uid_raw,
                        field=CONF_USER_ID,
                        agency=entry_agency,
                    )
                    break
                except (PassphraseRequiredError, CredentialDecryptionError):
                    continue
            if existing_uid is None:
                has_locked_same_host = True
                continue
        else:
            try:
                existing_uid, _ = decrypt_entry_credentials(
                    hass, entry_data, entry_agency
                )
            except (PassphraseRequiredError, CredentialDecryptionError):
                has_locked_same_host = True
                continue

        if hmac.compare_digest(existing_uid.encode("utf-8"), plain_uid.encode("utf-8")):
            raise UserExist

    if has_locked_same_host:
        raise LockedEntriesExist


async def _verify_and_encrypt(
    hass: HomeAssistant,
    *,
    plain_uid: str,
    plain_pin: str,
    host: str,
    agency: str,
    municipality: str,
    passphrase: str,
    exclude_entry_id: str | None = None,
) -> tuple[str, str, str, str]:
    await hass.async_add_executor_job(
        _check_existing_entries_sync,
        hass,
        plain_uid,
        host,
        passphrase,
        exclude_entry_id,
    )

    my_library = Library(
        plain_uid,
        plain_pin,
        host,
        agency,
        libraryName=municipality,
        account_label=municipality,
    )
    try:
        if not await hass.async_add_executor_job(my_library.login):
            raise InvalidAuth
    finally:
        await hass.async_add_executor_job(my_library.session.close)

    return await hass.async_add_executor_job(
        _encrypt_credentials_sync,
        hass,
        plain_uid,
        plain_pin,
        host,
        agency,
        passphrase,
    )


async def validate_input(
    hass: HomeAssistant,
    data: dict[str, Any],
    libraries: dict[str, dict[str, str]],
    *,
    exclude_entry_id: str | None = None,
) -> dict[str, Any]:
    """Validate credentials, close temporary HTTP session, and encrypt stored secrets."""
    payload = dict(data)
    payload[CONF_HOST] = libraries[payload[CONF_MUNICIPALITY]][CONF_HOST]
    payload[CONF_AGENCY] = libraries[payload[CONF_MUNICIPALITY]][CONF_AGENCY]
    payload[CONF_UPDATE_INTERVAL] = (
        payload[CONF_UPDATE_INTERVAL]
        if payload.get(CONF_UPDATE_INTERVAL)
        else UPDATE_INTERVAL
    )
    payload.setdefault(CONF_EXPOSE_PERSONAL_INFO, False)

    plain_uid = re.sub(r"\D", "", str(payload[CONF_USER_ID]))
    plain_pin = re.sub(r"\D", "", str(payload[CONF_PINCODE]))
    explicit_passphrase = str(payload.pop(CONF_ENCRYPTION_PASSPHRASE, "") or "")
    use_escrow = bool(payload.pop(CONF_USE_ESCROW_PASSPHRASE, False))
    passphrase = explicit_passphrase or (
        str(hass.data.get(DATA_ESCROW_PASSPHRASE, "") or "") if use_escrow else ""
    )

    enc_uid, enc_pin, mode, unique_id = await _verify_and_encrypt(
        hass,
        plain_uid=plain_uid,
        plain_pin=plain_pin,
        host=payload[CONF_HOST],
        agency=payload[CONF_AGENCY],
        municipality=payload[CONF_MUNICIPALITY],
        passphrase=passphrase,
        exclude_entry_id=exclude_entry_id,
    )

    payload[CONF_USER_ID] = enc_uid
    payload[CONF_PINCODE] = enc_pin
    payload[CONF_ENCRYPTION_MODE] = mode

    title = (
        f"{payload[CONF_MUNICIPALITY]} ({payload[CONF_NAME]})"
        if payload.get(CONF_NAME)
        else payload[CONF_MUNICIPALITY]
    )
    return {
        "title": title,
        "data": payload,
        "unique_id": unique_id,
        "passphrase": passphrase,
    }


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Bibliotek."""

    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlow:
        return OptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        try:
            libraries, exc_libraries = await self.hass.async_add_executor_job(
                refreshLibraries
            )
        except CannotConnect:
            errors["base"] = "cannot_connect"
            libraries, exc_libraries = {}, {}

        municipality = ""
        try:
            municipality = await self.hass.async_add_executor_job(
                municipalityFromCoor,
                self.hass.config.longitude,
                self.hass.config.latitude,
            )
            if municipality in exc_libraries:
                raise gatewayf
        except gatewayf:
            errors["base"] = "gatewayf"

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input, libraries)
            except UserExist:
                errors["base"] = "user_exist"
            except LockedEntriesExist:
                errors["base"] = "locked_entries_exist"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(info["unique_id"])
                self._abort_if_unique_id_configured()
                if info["passphrase"]:
                    self.hass.data.setdefault(DATA_PASSPHRASES, {})[
                        info["unique_id"]
                    ] = info["passphrase"]
                return self.async_create_entry(title=info["title"], data=info["data"])

        user_fields: dict[Any, Any] = {
            vol.Optional(CONF_NAME, default=""): str,
            vol.Required(
                CONF_MUNICIPALITY, default=municipality
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(libraries.keys()),
                    mode=selector.SelectSelectorMode.DROPDOWN,
                ),
            ),
            vol.Required(CONF_USER_ID, default=""): _SECRET_TEXT_SELECTOR,
            vol.Required(CONF_PINCODE, default=""): _SECRET_TEXT_SELECTOR,
        }
        if bool(self.hass.data.get(DATA_ESCROW_PASSPHRASE)):
            user_fields[vol.Optional(CONF_USE_ESCROW_PASSPHRASE, default=True)] = bool
        user_fields.update(
            {
                vol.Optional(
                    CONF_ENCRYPTION_PASSPHRASE, default=""
                ): _PASSPHRASE_SELECTOR,
                vol.Optional(CONF_SHOW_LOANS, default=True): bool,
                vol.Optional(CONF_SHOW_ELOANS, default=True): bool,
                vol.Optional(CONF_SHOW_DEBTS, default=True): bool,
                vol.Optional(CONF_SHOW_RESERVATIONS, default=True): bool,
                vol.Optional(CONF_EXPOSE_PERSONAL_INFO, default=False): bool,
                vol.Optional(CONF_UPDATE_INTERVAL, default=UPDATE_INTERVAL): int,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(user_fields),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any] | None = None
    ) -> FlowResult:
        """Handle reauthentication when credentials or passphrase are needed."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Unlock a passphrase-protected entry or re-enter library credentials."""
        reauth_entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        agency = str(reauth_entry.data.get(CONF_AGENCY, ""))
        host = str(reauth_entry.data.get(CONF_HOST, ""))
        municipality = str(reauth_entry.data.get(CONF_MUNICIPALITY, ""))
        passphrase_mode = reauth_entry.data.get(
            CONF_ENCRYPTION_MODE
        ) == ENCRYPTION_MODE_PASSPHRASE or is_passphrase_encrypted(
            reauth_entry.data.get(CONF_USER_ID)
        )

        if passphrase_mode:
            if user_input is not None:
                phrase = str(user_input.get(CONF_ENCRYPTION_PASSPHRASE, "") or "")
                try:
                    await self.hass.async_add_executor_job(
                        decrypt_entry_credentials,
                        self.hass,
                        reauth_entry.data,
                        agency,
                        phrase,
                    )
                except (PassphraseRequiredError, CredentialDecryptionError):
                    errors["base"] = "invalid_passphrase"
                else:
                    self.hass.data.setdefault(DATA_PASSPHRASES, {})[
                        reauth_entry.entry_id
                    ] = phrase
                    return self.async_update_reload_and_abort(
                        reauth_entry, reason="reauth_successful"
                    )

            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=vol.Schema(
                    {
                        vol.Required(
                            CONF_ENCRYPTION_PASSPHRASE, default=""
                        ): _PASSPHRASE_SELECTOR
                    }
                ),
                errors=errors,
            )

        if user_input is not None:
            plain_uid = re.sub(r"\D", "", str(user_input[CONF_USER_ID]))
            plain_pin = re.sub(r"\D", "", str(user_input[CONF_PINCODE]))
            phrase = str(user_input.get(CONF_ENCRYPTION_PASSPHRASE, "") or "")
            try:
                enc_uid, enc_pin, mode, new_uid = await _verify_and_encrypt(
                    self.hass,
                    plain_uid=plain_uid,
                    plain_pin=plain_pin,
                    host=host,
                    agency=agency,
                    municipality=municipality,
                    passphrase=phrase,
                    exclude_entry_id=reauth_entry.entry_id,
                )
            except UserExist:
                errors["base"] = "user_exist"
            except LockedEntriesExist:
                errors["base"] = "locked_entries_exist"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected reauth exception")
                errors["base"] = "unknown"
            else:
                passphrases = self.hass.data.setdefault(DATA_PASSPHRASES, {})
                if phrase:
                    passphrases[reauth_entry.entry_id] = phrase
                else:
                    passphrases.pop(reauth_entry.entry_id, None)
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={
                        CONF_USER_ID: enc_uid,
                        CONF_PINCODE: enc_pin,
                        CONF_ENCRYPTION_MODE: mode,
                    },
                    unique_id=new_uid,
                    reason="reauth_successful",
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USER_ID, default=""): _SECRET_TEXT_SELECTOR,
                    vol.Required(CONF_PINCODE, default=""): _SECRET_TEXT_SELECTOR,
                    vol.Optional(
                        CONF_ENCRYPTION_PASSPHRASE, default=""
                    ): _PASSPHRASE_SELECTOR,
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Update credentials or switch between keyfile and passphrase encryption."""
        reconfigure_entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        try:
            libraries, _ = await self.hass.async_add_executor_job(refreshLibraries)
        except CannotConnect:
            errors["base"] = "cannot_connect"
            libraries = {}

        current_municipality = str(reconfigure_entry.data.get(CONF_MUNICIPALITY, ""))
        if current_municipality and current_municipality not in libraries:
            libraries[current_municipality] = {
                CONF_HOST: reconfigure_entry.data.get(CONF_HOST, ""),
                CONF_AGENCY: reconfigure_entry.data.get(CONF_AGENCY, ""),
            }

        if user_input is not None:
            merged = {
                **reconfigure_entry.data,
                **user_input,
            }
            try:
                info = await validate_input(
                    self.hass,
                    merged,
                    libraries,
                    exclude_entry_id=reconfigure_entry.entry_id,
                )
            except UserExist:
                errors["base"] = "user_exist"
            except LockedEntriesExist:
                errors["base"] = "locked_entries_exist"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected reconfigure exception")
                errors["base"] = "unknown"
            else:
                passphrases = self.hass.data.setdefault(DATA_PASSPHRASES, {})
                if info["passphrase"]:
                    passphrases[reconfigure_entry.entry_id] = info["passphrase"]
                else:
                    passphrases.pop(reconfigure_entry.entry_id, None)
                return self.async_update_reload_and_abort(
                    reconfigure_entry,
                    data_updates=info["data"],
                    unique_id=info["unique_id"],
                    reason="reconfigure_successful",
                )

        reconf_fields: dict[Any, Any] = {
            vol.Required(
                CONF_MUNICIPALITY, default=current_municipality
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(libraries.keys()),
                    mode=selector.SelectSelectorMode.DROPDOWN,
                ),
            ),
            vol.Required(CONF_USER_ID, default=""): _SECRET_TEXT_SELECTOR,
            vol.Required(CONF_PINCODE, default=""): _SECRET_TEXT_SELECTOR,
        }
        if bool(self.hass.data.get(DATA_ESCROW_PASSPHRASE)):
            reconf_fields[vol.Optional(CONF_USE_ESCROW_PASSPHRASE, default=True)] = bool
        reconf_fields[vol.Optional(CONF_ENCRYPTION_PASSPHRASE, default="")] = (
            _PASSPHRASE_SELECTOR
        )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(reconf_fields),
            errors=errors,
        )


class OptionsFlow(config_entries.OptionsFlow):
    """Handle Bibliotek non-credential display and polling options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            # Persist only non-credential options; never duplicate data into options.
            clean_options = {
                key: user_input[key] for key in OPTION_KEYS if key in user_input
            }
            return self.async_create_entry(title="", data=clean_options)

        current = {**self.config_entry.data, **self.config_entry.options}
        options_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SHOW_LOANS, default=current.get(CONF_SHOW_LOANS, True)
                ): bool,
                vol.Optional(
                    CONF_SHOW_ELOANS, default=current.get(CONF_SHOW_ELOANS, True)
                ): bool,
                vol.Optional(
                    CONF_SHOW_DEBTS, default=current.get(CONF_SHOW_DEBTS, True)
                ): bool,
                vol.Optional(
                    CONF_SHOW_RESERVATIONS,
                    default=current.get(CONF_SHOW_RESERVATIONS, True),
                ): bool,
                vol.Optional(
                    CONF_EXPOSE_PERSONAL_INFO,
                    default=current.get(CONF_EXPOSE_PERSONAL_INFO, False),
                ): bool,
                vol.Optional(
                    CONF_UPDATE_INTERVAL,
                    default=current.get(CONF_UPDATE_INTERVAL, UPDATE_INTERVAL),
                ): int,
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                options_schema, self.config_entry.options
            ),
        )


class gatewayf(HomeAssistantError):
    """Error to indicate municipality is using gatewayf."""


class UserExist(HomeAssistantError):
    """Error to indicate user allready exist."""


class LockedEntriesExist(HomeAssistantError):
    """Error to indicate locked passphrase-protected entries exist for this host."""


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
