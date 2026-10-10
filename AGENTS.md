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
├── library_api.py     # Library client: login, tokens, fetchLoans/Reservations/Debts/UserInfo
├── sensor.py          # DataUpdateCoordinator + the sensor entities
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

- **`library_api.py` is one large, stateful class.** `Library` keeps mutable
  class-level defaults and instance state across polls. `login()` and token
  refresh mutate `self.loggedIn`, `self._user_token`, `self._user_token_exp`.
  Be careful: `_set_tokens` only sets `_user_token_exp` when a `"user"` token is
  present, so `user_token` can compare against an attribute that was never set.
- **`DEBUG = True` is hardcoded** at the top of `library_api.py`. It is not wired
  to HA logging; leave it unless the task is specifically about logging.
- **`sensor.py` has dead config gates:** `if True:  # new_data[CONF_SHOW_LOANS]`
  and similar. The show/hide options currently do nothing for entity creation.
- **`libraryMaterial.url` and `libraryDebt.feeType` are now populated** from the
  API payloads (`ownerWork.workId` → bibliotek.dk work URL; eReolen ISBN →
  bibliotek.dk search; `debt["type"]` → fee type). Keep these in sync if the DBC
  response shape changes.
- **`config_flow.py` no longer calls `SystemExit`** on HTTP failures; it raises
  `CannotConnect`, which the flow maps to `errors["base"] = "cannot_connect"`.
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
