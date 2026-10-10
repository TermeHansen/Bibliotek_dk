# Improvement Plan — Bibliotek_dk

Findings from a read-through of the whole repo (v0.6.1). Ordered so the
highest-impact, lowest-risk work lands first. Each item names the file(s) and a
concrete change.

> **Status:** Sections 1 and 2 are done (see `CHANGELOG.md` 0.6.2). The URL /
> fee-type field names were confirmed against the live DBC APIs and the library
> frontend's own `dpl-react` bundle.

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

Still open: config-flow tests that mock `requests` for the library list and
municipality lookup (currently `validate_input` is tested with a fake `Library`).

---

## 3. Robustness / resilience

- **No retry or timeout** on any `requests` call. Add a session-level timeout and
  a small retry/backoff for transient 5xx, or rely on the coordinator's
  `last_update_success` to mark entities unavailable (already done) plus logging.
- **Silent failures:** most `if res.status_code == 200:` blocks have no `else`.
  A non-200 leaves the previous state in place with no log. Add `else: _LOGGER.warning(...)`.
- **`_getDetails` caching** (`self._details`) grows unbounded for the process
  lifetime. Bound it (e.g. `functools.lru_cache` or a size cap).
- **`update()` re-fetches user info only when `self.user.name` is falsy** — if the
  first fetch fails, it retries forever (fine) but never refreshes profile data
  otherwise. Decide whether profile fields should refresh periodically.
- **`session` is never closed on unload.** `async_unload_entry` pops the entry but
  the `requests.Session` lives on. Close it in unload.

---

## 4. Architecture / code quality

- **`Library` is a god-object** mixing HTTP, auth, parsing, and domain models.
  Split into: a transport/session layer (auth + tokens), a per-resource fetcher,
  and plain dataclasses for `libraryLoan` / `libraryReservation` / `libraryDebt`.
  This is a bigger refactor — do it behind tests from section 2.
- **Class-level mutable defaults** (`loans = []`, `debts = []` on `libraryUser`)
  are shared until an instance assigns its own. Convert to instance state in
  `__init__` to avoid cross-instance aliasing.
- **`sensor.py` entity classes are ~90% duplicated.** Five sensors repeat the same
  `name/icon/unique_id/should_poll/available/async_update/async_added_to_hass`.
  Extract a `BibliotekBaseSensor(SensorEntity)` with a shared `CoordinatorEntity`
  base (HA's `CoordinatorEntity` gives `available` + listener wiring for free).
- **`if True:  # new_data[CONF_SHOW_LOANS]`** gates in `sensor.async_setup_entry`:
  the show/hide options are dead. Either honor the options (skip creating the
  entity) or remove the options and the commented code.
- **`DEBUG`** should be replaced by HA's `_LOGGER.debug` gating, not a module flag.

---

## 5. Repo hygiene / tooling

- **`__init__.py` and `config_flow.py` docstrings say "Dummy Garage"** (copy-paste
  from a template). Fix to "Bibliotek".
- **Add `ruff format`** to CI (Ruff is configured but only `check` runs).
- **No `hacs.json` `version`** — fine (HACS reads `manifest.json`), but confirm the
  `homeassistant` min version if you adopt newer HA APIs.
- **README** is decent but the "still in development" note and the sensor list
  predate eReolen/debts options. Refresh it and document the `show_*` options.
- **CHANGELOG** uses a hand-maintained `<details>` collapse; consider
  auto-generating from tags (the repo already tags releases: `v0.6.1`, …).
- **`.gitignore`** still contains unrelated template entries (`renoweb.code-workspace`,
  `affalddk.code-workspace`). Trim.
- **Branch cleanup:** remote branches `cleanup`, `new_API`, `no_ereol` look merged;
  verify and delete.

---

## 6. Feature ideas (optional, user-facing)

- Config option for the **update interval already exists** — surface it in the
  options flow with the current value (partially done via suggested values).
- **Renew a loan** as an HA service (the API exposes `renewId`/`renewAble` but
  nothing uses them).
- **Debts as a proper currency sensor** with `device_class: monetary` and
  `state_class`, instead of a raw float state.
- **Diagnostics** support (`async_get_config_entry_diagnostics`) with secrets
  redacted — helps users file useful bug reports.
- **eReolen reservation expiry** parsing in `fetchReservations` drops `ignoretz`
  (`library_api.py:337-338`) unlike the physical path — normalize timezone handling.

---

## Suggested order

1. Section 1 (bug fixes) — small, safe, immediate.
2. Section 2 (tests) — unlocks everything else.
3. Section 3 (robustness) — reduces flaky-user reports.
4. Sections 4–5 (refactor + hygiene) — steady cleanup.
5. Section 6 — as time allows.
