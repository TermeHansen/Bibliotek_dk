# Improvement Plan — Bibliotek_dk

Findings from a read-through of the whole repo (v0.6.1). Ordered so the
highest-impact, lowest-risk work lands first. Each item names the file(s) and a
concrete change.

---

## 1. Correctness bugs (fix first — small, self-contained)

### 1.1 Dead attributes shipped to sensors
`libraryMaterial.url` and `libraryDebt.feeType` are read in `sensor.py`
(`url` on every loan/reservation/debt; `fee_type` on debts) but **never assigned**
in `library_api.py`. Every consumer sees `None`.

- `libraryMaterial.__init__` (`library_api.py:386`): parse a URL from the API
  payload (eReolen `product` has a web URL; physical `manifestation` has `pid`,
  which maps to `https://bibliotek.dk/...`). Assign `self.url`.
- `libraryDebt`: populate `feeType` from the fee object in `fetchDebts`
  (`library_api.py:345`) — the DBC fees payload carries the fee type/`feeType`.

### 1.2 `use_eReolen` is not applied on first load
`__init__.py` builds `Library(...)` with the class default `use_eReolen = True`.
The real value is only set later in `sensor.async_setup_entry` from options. The
first `coordinator.async_request_refresh()` in `sensor.py:62` runs after that, so
it works — but the ordering is implicit and easy to break. Pass the option into
`Library.__init__` explicitly.

### 1.3 Token-expiry edge case
`_set_tokens` (`library_api.py:157`) sets `_user_token_exp` only inside
`if '"user"' in res.text`. `user_token`/`library_token` then compare
`self._user_token_exp < now`, which raises `AttributeError` if a "user" token was
never seen. Initialize `_user_token_exp` in `__init__` and guard the comparison.

### 1.4 Login error handling
- `library_api.py:126`: the error string is `"f({self.user.date}) Failed to login..."` —
  a literal `f(` typo, so the placeholder never interpolates.
- `library_api.py:231`: `self.user.dat` typo (should be `self.user.date`).
- `config_flow.py`: `raise SystemExit(err)` on `HTTPError`/`RequestException`
  (lines 110, 118) will kill the flow host instead of surfacing a config error.
  Replace with `raise CannotConnect` and let the flow map it to `errors["base"]`.

### 1.5 `logout()` debug line
`library_api.py:212`: `f"... {~self.loggedIn}"` uses bitwise `~` on a str/None
and references `url` which may be unbound. Simplify to a boolean log.

### 1.6 Duplicate `_LOGGER` assignment
`library_api.py:17-18` and `sensor.py:31-32` assign `_LOGGER` twice in a row
(`__package__` then `__name__`). Drop the first line in each.

---

## 2. Test coverage (highest long-term value)

There is **no test suite**; CI only lints. For an integration that talks to a
third-party API and breaks on auth changes (see the last several commits), this
is the biggest gap.

- Add `tests/` with `pytest` + `pytest-asyncio` + `freezegun` (already in
  `environment.yaml` but unused).
- Use HA's `pytest-homeassistant-custom-component` fixture set for config-flow
  and coordinator tests.
- Start with pure-logic units that need no network:
  - `sortLists()` ordering (loans by expire, reservations by queue).
  - `libraryMaterial` parsing for both eReolen and `manifestation` payloads.
  - `md5_unique_id` stability.
  - `_branchName` id-splitting (`"a-b-123"` → `"123"`).
- Then config-flow tests with mocked `requests` (library list + municipality lookup).
- Wire `pytest` into `python-testing.yml` alongside Ruff.
- Keep captured API fixtures **anonymized** (no CPR, PIN, or tokens).

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
