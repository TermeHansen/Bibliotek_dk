# Improvement Plan — Bibliotek_dk

Findings from a read-through of the whole repo (v0.6.1). Ordered so the
highest-impact, lowest-risk work lands first. Each item names the file(s) and a
concrete change.

> **Status:** Sections 1–4 and 6 are done, plus the Section 5 items noted below
> (see `CHANGELOG.md` 0.6.2 / 0.7.0 / 0.8.0). The URL / fee-type field names and
> the renew endpoint were confirmed against the live DBC APIs and the library
> frontend's own `dpl-react` bundle. The only remaining Section 5 item is the
> remote branch cleanup (a remote operation, deliberately not done here).

---

## 1. Correctness bugs (fix first — small, self-contained) — DONE

### 1.1 Dead attributes shipped to sensors — DONE
`libraryMaterial.url` and `libraryDebt.feeType` were read in `sensor.py` but
never assigned. Fixed by extending the GraphQL details query with
`ownerWork { workId }` and populating:
- physical materials → `https://bibliotek.dk/work/<workId>`
- eReolen materials → `https://bibliotek.dk/?q=<ISBN>` (the eReolen SPA has no
  server-side deep links; the ISBN comes from `externalProductId.id`)
- debts → `feeType` from `debt["type"]` (fees schema confirmed via the library's
  `dpl-react` bundle: `{feeId, amount, creationDate, dueDate, reasonMessage,
  type, payableByClient, materials}`).

### 1.2 `use_eReolen` is not applied on first load — DONE
`use_eReolen` is now passed into `Library.__init__` from `__init__.py`.

### 1.3 Token-expiry edge case — DONE
`_user_token_exp` is initialised to `None` in `__init__` and both token
properties guard the comparison.

### 1.4 Login error handling — DONE
`f(` typo and `self.user.dat` fixed; `SystemExit` replaced with `CannotConnect`
(mapped to `errors["base"] = "cannot_connect"`).

### 1.5 `logout()` debug line — DONE
Bitwise `~` and the possibly-unbound `url` removed from the log line.

### 1.6 Duplicate `_LOGGER` assignment — DONE
Duplicate `_LOGGER` lines removed from `library_api.py` and `sensor.py`.

---

## 2. Test coverage (highest long-term value) — DONE

`tests/` added, following the sibling `affalddk` repo: `conftest.py` stubs the
`homeassistant` package, so the suite runs **without a HA install**. Covers:
- `sortLists()` ordering (loans by expire, reservations by queue, ready by pickup).
- `libraryMaterial` parsing for both eReolen and `manifestation` payloads,
  including the new `url` fields and the missing-field cases.
- `md5_unique_id` stability, `_branchName` id-splitting, `use_eReolen` plumbing,
  the `_user_token_exp` regression.
- sensor state/attributes and `validate_input` (success, invalid auth, duplicate
  user, default interval).

`pytest` is wired into `python-testing.yml` (run under both `Europe/Copenhagen`
and `UTC`). Fixtures are synthetic/anonymised — no CPR, PIN, or tokens.

Config-flow HTTP parsing is now covered too: `refreshLibraries` and
`municipalityFromCoor` were extracted to module-level helpers and are tested with
a fake `requests` session (success, gatewayf split, HTTP error, timeout, missing
script). Fixtures are synthetic/anonymised — no CPR, PIN, or tokens.

---

## 3. Robustness / resilience — DONE

- **Timeout + retry:** every request now goes through `Library._get` / `_post`,
  which apply a `(10, 30)` connect/read timeout, and the session is mounted with
  a `urllib3.Retry` (3 attempts, backoff, 5xx + connection errors).
- **Silent failures:** every `if res.status_code == 200:` block that lacked an
  `else` now logs a warning with the status code (details, branch lookup, user
  info, loans, eReolen loans/products, reservations, eReolen reservations,
  debts, API-url discovery, token fetch).
- **`_getDetails` caching:** the cache is bounded (`DETAILS_CACHE_MAX = 500`,
  cleared when full) and a failed lookup returns `{}` instead of raising
  `KeyError` on the cache.
- **`update()` profile refresh:** the profile is fetched on the first poll and
  then at most once a day (`_user_info_fetched`), so branch/address/notification
  changes propagate without a restart.
- **`session` on unload:** closed in `async_unload_entry` (done in 0.6.2).
- **Extra:** eReolen reservation dates are parsed as naive UTC like the physical
  path (they were tz-aware, which could break the mixed-list sort); a fee with
  an empty `materials` list no longer crashes `fetchDebts`.
- **Extra:** municipality auto-detection was broken for everyone — the DAWA
  endpoint (`api.dataforsyningen.dk`) was retired 2026-10-01 and returned HTTP
  410. Switched to the `dawa.companydata.dk` mirror (as affalddk did).
- **Extra:** `Accept-Encoding` advertised `br`/`zstd`, which `requests` cannot
  decode without optional packages, so some responses arrived as undecodable
  bytes. Now only `gzip, deflate`.

---

## 4. Architecture / code quality — DONE

- **`Library` is a god-object** mixing HTTP, auth, parsing, and domain models.
  Split into: a transport/session layer (auth + tokens), a per-resource fetcher,
  and plain dataclasses for `libraryLoan` / `libraryReservation` / `libraryDebt`.
  This is a bigger refactor — do it behind tests from section 2. — DONE (0.7.0):
  `transport.py` (`LibraryTransport`: session, login, tokens, `_get`/`_post`,
  `REQUEST_TIMEOUT`), `models.py` (`libraryUser` + the material/loan/reservation/
  debt holders), and `library_api.py` keeps `Library` as a thin coordinator-facing
  facade over the transport plus the `fetch*` methods and the details/branch
  caches. The public API is unchanged (`Library`, the model names and
  `REQUEST_TIMEOUT` are re-exported from `library_api`), verified against the
  live account.
- **Class-level mutable defaults** (`loans = []`, `debts = []` on `libraryUser`)
  are shared until an instance assigns its own. Convert to instance state in
  `__init__` to avoid cross-instance aliasing. — DONE (0.7.0).
- **`sensor.py` entity classes are ~90% duplicated.** Five sensors repeat the same
  `name/icon/unique_id/should_poll/available/async_update/async_added_to_hass`.
  Extract a `BibliotekBaseSensor(SensorEntity)` with a shared `CoordinatorEntity`
  base (HA's `CoordinatorEntity` gives `available` + listener wiring for free).
  — DONE (0.7.0): `BibliotekBaseSensor` + `BibliotekCountSensor`; the file dropped
  from 544 to ~410 lines with no behavior change (verified against live data).
- **`if True:  # new_data[CONF_SHOW_LOANS]`** gates in `sensor.async_setup_entry`:
  the show/hide options are dead. Either honor the options (skip creating the
  entity) or remove the options and the commented code. — DONE (0.7.0: the gates
  now honor the options; covered by tests).
- **`DEBUG`** should be replaced by HA's `_LOGGER.debug` gating, not a module flag.
  — DONE (0.7.0): the flag is gone; `_debug` emits unconditionally and is gated by
  HA's logger config.

---

## 5. Repo hygiene / tooling

- **`__init__.py` and `config_flow.py` docstrings say "Dummy Garage"** (copy-paste
  from a template). Fix to "Bibliotek". — DONE (0.7.0).
- **Add `ruff format`** to CI (Ruff is configured but only `check` runs). — DONE
  (0.7.0): formatting applied across the codebase and `ruff format --check` added
  to `python-testing.yml`.
- **No `hacs.json` `version`** — fine (HACS reads `manifest.json`), but confirm the
  `homeassistant` min version if you adopt newer HA APIs. — DONE (0.8.0):
  `hacs.json` declares `"homeassistant": "2024.1.0"`.
- **README** is decent but the "still in development" note and the sensor list
  predate eReolen/debts options. Refresh it and document the `show_*` options.
  — DONE (0.7.0).
- **CHANGELOG** uses a hand-maintained `<details>` collapse; consider
  auto-generating from tags (the repo already tags releases: `v0.6.1`, …).
  — Decided against (0.8.0): kept hand-maintained.
- **`.gitignore`** still contains unrelated template entries (`renoweb.code-workspace`,
  `affalddk.code-workspace`). Trim. — DONE (0.7.0).
- **Branch cleanup:** remote branches `cleanup`, `new_API`, `no_ereol` look merged;
  verify and delete. — Still open (needs a remote operation; not done).

---

## 6. Feature ideas (optional, user-facing)

- Config option for the **update interval already exists** — surface it in the
  options flow with the current value (partially done via suggested values).
- **Renew a loan** as an HA service (the API exposes `renewId`/`renewAble` but
  nothing uses them). — DONE (0.8.0): `bibliotek_dk.renew_loans` service.
- **Debts as a proper currency sensor** with `device_class: monetary` and
  `state_class`, instead of a raw float state. — DONE (0.8.0).
- **Diagnostics** support (`async_get_config_entry_diagnostics`) with secrets
  redacted — helps users file useful bug reports. — DONE (0.8.0).
- **eReolen reservation expiry** parsing in `fetchReservations` drops `ignoretz`
  (`library_api.py:337-338`) unlike the physical path — normalize timezone handling.
  — DONE (0.7.0).
- **Group sensors under an HA device** (one device per config entry) so they are
  not loose entities. — DONE (0.8.0).

---

## Suggested order

1. Section 1 (bug fixes) — small, safe, immediate.
2. Section 2 (tests) — unlocks everything else.
3. Section 3 (robustness) — reduces flaky-user reports.
4. Sections 4–5 (refactor + hygiene) — steady cleanup.
5. Section 6 — as time allows.
