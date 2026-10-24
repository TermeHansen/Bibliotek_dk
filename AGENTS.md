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
loans_card.yaml        # drop-in auto-entities card (self-discovers the sensor)
loans_card_decluttering.yaml  # reusable decluttering-card template
tests/                 # pytest suite; conftest.py stubs homeassistant (no HA needed)
```

## Commands

Dependencies are managed with **uv** (`pyproject.toml` is the source of truth,
`uv.lock` is committed, CI runs `uv sync --group dev --locked`). The test suite
stubs the `homeassistant` package (`tests/conftest.py`), so it runs **without a
Home Assistant install**.

```bash
# One-time setup (creates .venv from uv.lock)
uv sync --group dev

# Lint (this is what CI runs; must pass)
uv run ruff check --config pyproject.toml
uv run ruff format --check custom_components tests   # CI also enforces formatting

# Tests (no HA needed; run in both timezones like CI does)
TZ=Europe/Copenhagen uv run pytest tests --disable-warnings
TZ=UTC uv run pytest tests --disable-warnings

# After changing a dependency
uv lock && uv sync --group dev
```

If pytest dies with `ModuleNotFoundError` (`dateutil`, `bs4`, `voluptuous`),
you are outside the uv venv — use `uv run` or `uv sync` first, not the bare
system python. A conda route still works locally (`environment.yaml` mirrors
the same dependency set for conda users), but CI and the docs above use uv.

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
  entry unloads. It is registered with `supports_response=OPTIONAL` so
  automations can read the per-loan status back. It calls `Library.renewLoans()`,
  which POSTs a *bare JSON array* of loan ids to `URL_LOANS_RENEW` (the DBC API
  rejects a `{"data": [...]}` wrapper) and maps the response to
  `{loanId: renewalStatus}`. The handler raises `HomeAssistantError` when nothing
  renewed, and on success asks the entry's coordinator (`library.coordinator`,
  set in `sensor.async_setup_entry`) for a refresh — it does **not** reload the
  entry, which would rebuild the `Library` and drop the last-good results kept
  for API outages. `renew_id` on the loan sensor attributes is the API `loanId`.
- **`diagnostics.py` redacts `CONF_USER_ID`, `CONF_PINCODE`, `CONF_NAME` and the
  patron `name` key** via `async_redact_data`; keep any new credential or
  identity keys in its `TO_REDACT` set.
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
  The silent-auth-failure issue (plan 7.1) is fixed as of 0.8.1: a token response
  without a user token clears the stale token, `update()` raises
  `LibraryAuthError`, and `sensor.py` maps it to `UpdateFailed` so the sensors go
  unavailable — the wrong-PIN path and the token-expiry re-login were verified
  against two real accounts on 2026-10-24.
- **Known open findings are tracked in `IMPROVEMENT_PLAN.md` Section 7** (from
  the 2026-10-23 full review; 7.1/7.2/7.4/7.5/7.6 fixed in 0.8.1). Read it
  before changing the fetchers: eReolen products are fetched one request per
  item per poll with no cache (7.3), and the `_getDetails` fallback host is
  suspect (7.9).
- **`_getDetails` falls back to a staging host** (`temp.fbi-api.dbc.dk`) when
  the scraped `data-fbi-global-base-url` is missing; treat that fallback as
  suspect (plan 7.9).
- **`renew_id` is exposed only on the loans sensor**, not the overdue sensor —
  overdue items are the same loans, so the id is available if a future renew UX
  needs it.
- **The remote branches `cleanup`, `new_API`, `no_ereol` are NOT merged** (3 /
  19 / 6 unique commits ahead of `improvements2`); diff them against the
  current code before deleting anything (plan Section 5).
- **The repo is still a GitHub fork** of `J-Lindvig/Bibliotek_dk` (upstream
  inactive since 2024-03, not archived; detach requested via GitHub support
  2026-10-23). Don't add an `upstream` remote or sync from it; attribution is
  the README Credits section, `CREDITS` in `const.py`, and `codeowners` in
  `manifest.json`.

## Scope discipline

- This is a HACS integration consumed by real users' Home Assistant instances.
  Prefer minimal, targeted diffs over refactors.
- Don't touch `dashboard.yaml` / `loans_card.yaml` unless the task is about the
  sample dashboards.
- Never commit real CPR numbers, PIN codes, tokens, or captured API responses.
- The gitignored `secrets` file at the repo root holds real test credentials
  (`user_id pin` per line) for manual real-data runs; it must stay ignored and
  its contents must never be printed or committed.
