# AGENTS.md — Bibliotek_dk

Guidance for AI coding agents working in this repository.

## What this is

A Home Assistant **custom integration** (HACS) that exposes Danish public
library data — loans, reservations, eReolen e-books/audiobooks, and fees — as
HA sensors. It logs into a patron's library account over HTTP and scrapes /
calls DBC's open-platform APIs.

- Domain: `bibliotek_dk`
- Integration class: `cloud_polling` (a `DataUpdateCoordinator` polls on an interval)
- Language: Python 3.12
- Runtime deps: `beautifulsoup4`, plus HA's bundled `requests`, `voluptuous`, `python-dateutil`

## Layout

```
custom_components/bibliotek_dk/
├── __init__.py        # async_setup_entry / unload; stores one Library per config entry
├── config_flow.py     # UI config + options flow; library list scraping; municipality lookup
├── const.py           # CONF_* keys, headers, GraphQL query strings, URLs
├── transport.py       # LibraryTransport: session, login, tokens, _get/_post
├── models.py          # libraryUser + libraryMaterial/Loan/Reservation/Debt holders
├── library_api.py     # Library facade: update(), fetch* methods, caches
├── sensor.py          # DataUpdateCoordinator + the sensor entities
├── diagnostics.py     # async_get_config_entry_diagnostics (credentials redacted)
├── services.yaml      # bibliotek_dk.renew_loans service schema
├── manifest.json      # version, requirements, codeowners
├── strings.json       # translation source (HA requires this to mirror translations/)
└── translations/{en,da}.json
dashboard.yaml         # sample Lovelace dashboard (markdown templates)
loans_card.yaml        # sample auto-entities card
tests/                 # pytest suite; conftest.py stubs homeassistant (no HA needed)
```

## Commands

Environment is conda/micromamba (`environment.yaml`). The test suite stubs the
`homeassistant` package (`tests/conftest.py`), so it runs **without a Home
Assistant install**.

```bash
# Lint (this is what CI runs; must pass)
ruff check --config pyproject.toml
ruff format --check custom_components tests   # CI also enforces formatting

# Tests (no HA needed; run in both timezones like CI does)
TZ=Europe/Copenhagen pytest tests --disable-warnings
TZ=UTC pytest tests --disable-warnings

# Create the dev environment
micromamba create -f environment.yaml     # or: conda env create -f environment.yaml
```

CI (`.github/workflows/`): `validate.yaml` runs Home Assistant **hassfest** and
**HACS** validation; `python-testing.yml` runs Ruff and pytest. All must pass.

## Conventions

- Match the existing style: `from __future__ import annotations`, `_LOGGER =
  logging.getLogger(__name__)`, module-level `CONF_*` constants in `const.py`.
- Keep blocking I/O (`requests`, BeautifulSoup) synchronous, and call it from HA
  only via `hass.async_add_executor_job(...)`. Never call it directly on the
  event loop.
- New user-facing strings go in `strings.json` **and** `translations/en.json`
  **and** `translations/da.json`. Keep them in sync.
- Any change that affects user-visible behavior should bump `version` in
  `manifest.json` and add a `CHANGELOG.md` entry (keep the newest version at the
  top; older entries live in the collapsed `<details>` block).
- Ruff config lives in `pyproject.toml` (`E,F,T,B,S` selected; `T201,S101,E501,B006`
  ignored). Don't widen the ignore list to silence a real finding.

## Gotchas (read before editing)

- **The client is split across three modules.** `transport.py` holds
  `LibraryTransport` (session, `login()`, tokens, `_get`/`_post` with
  `REQUEST_TIMEOUT`, and the `urllib3.Retry` adapter). `models.py` holds the dumb
  data holders (`libraryUser`, `libraryMaterial`/`Loan`/`Reservation`/
  `ReservationReady`/`Debt`). `library_api.py` keeps `Library` — the
  coordinator-facing facade that owns the per-account state (`user`, `branches`,
  `_urls`, `_details`, `_user_info_fetched`) and the `fetch*` methods. `Library`
  exposes `.host`, `.session`, `.login()`, `.logout()` as passthroughs to its
  transport, and re-exports the model names plus `REQUEST_TIMEOUT` so existing
  imports (`from .library_api import libraryLoan`) keep working. `sensor.py`
  imports the models from `.models` directly.
- **`Library` keeps instance state across polls.** `login()` and token refresh
  mutate `self._transport.loggedIn`, `_user_token`, `_user_token_exp`.
  `_user_token_exp` is `None` until a user token is issued; the token properties
  guard against that. All HTTP goes through `LibraryTransport._get` / `_post`,
  which apply the `REQUEST_TIMEOUT` and route through a session mounted with a
  `urllib3.Retry` adapter — use them instead of calling `self.session.get/post`
  directly.
- **Logging is f-string based** throughout; `_LOGGER.debug(...)` is always emitted
  (no module-level DEBUG flag). Gate output via HA's logger config
  (`custom_components.bibliotek_dk: debug`). `LibraryTransport._debug` just
  prefixes the patron date.
- **`update()` refreshes the profile at most once a day** (`_user_info_fetched`),
  and on the first poll; the timestamp only advances on a successful fetch. The
  `fetch*` methods keep the previous state on a failed request and log a warning.
  The physical (DBC) and eReolen sources are cached separately in
  `_loans`/`_loans_overdue`/`_reservations`/`_reservations_ready` (keyed by
  source), and the `user` lists are the merged successful results — so one
  failing API does not discard the other's fresh data.
- **A kept-without-details item has `title is None`** (a failed `_getDetails`
  returns `{}`); `sortLists()` falls back to `""` so a sort key never mixes
  `None` with `str`.
- **`_details` is a bounded cache** (`DETAILS_CACHE_MAX`); `_getDetails` returns
  `{}` on failure instead of raising.
- **`sensor.py` honors the show/hide options** when creating entities (0.7.0).
  All sensors derive from `BibliotekBaseSensor` (coordinator wiring) and the
  count sensors from `BibliotekCountSensor`; a new count sensor only needs
  `_items`, `_item_attrs`, the icon variants and its name/prefix. Every sensor
  is grouped under one HA device per config entry (`_attr_device_info` is set in
  `async_setup_entry`, keyed on `entry.entry_id`).
- **The `renew_loans` service is registered globally** in `__init__.py` (one
  handler for all entries, keyed by `config_entry_id`) and removed when the last
  entry unloads. It calls `Library.renewLoans()`, which POSTs a *bare JSON array*
  of loan ids to `URL_LOANS_RENEW` (the DBC API rejects a `{"data": [...]}`
  wrapper) and maps the response to `{loanId: renewalStatus}`. The handler
  raises `HomeAssistantError` when nothing renewed. `renew_id` on the loan
  sensor attributes is the API `loanId`.
- **`diagnostics.py` redacts `CONF_USER_ID` and `CONF_PINCODE`** via
  `async_redact_data`; keep any new credential keys in its `TO_REDACT` set.
- **`libraryMaterial.url` and `libraryDebt.feeType` are populated** from the
  API payloads (`ownerWork.workId` → bibliotek.dk work URL; eReolen ISBN →
  bibliotek.dk search; `debt["type"]` → fee type). Keep these in sync if the DBC
  response shape changes.
- **`config_flow.py` no longer calls `SystemExit`** on HTTP failures; it raises
  `CannotConnect`, which the flow maps to `errors["base"] = "cannot_connect"`. The
  library-list and municipality lookups are module-level helpers
  (`refreshLibraries`, `municipalityFromCoor`) with timeouts, tested with a fake
  `requests` session.
- **`hass.data[DOMAIN]` is keyed by `entry_id`.** The duplicate-user check in
  `validate_input` iterates those values, so it only sees *loaded* entries.
- **Options flow writes into `entry.data`**, not `entry.options`; the coordinator
  merges both (`{**entry.data, **entry.options}`). Keep that in mind when touching
  config handling.
- **`strings.json` uses `[%key:...%]` references** to HA common translations —
  that's intentional, not a placeholder to fill in.
- **Token expiry / auth is the most fragile area** (see recent commits). Changes
  there need manual testing against a real account; there is no automated coverage.

## Scope discipline

- This is a HACS integration consumed by real users' Home Assistant instances.
  Prefer minimal, targeted diffs over refactors.
- Don't touch `dashboard.yaml` / `loans_card.yaml` unless the task is about the
  sample dashboards.
- Never commit real CPR numbers, PIN codes, tokens, or captured API responses.
