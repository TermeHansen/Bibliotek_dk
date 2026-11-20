# Changelog for Bibliotek DK Home Assistant Integration

## Version 0.9.2

**Date**: `2026-11-21`

### Changes

* Material details (title, creators, cover, type, work URL) now come from the
  Biblo app's record endpoint (`api.detdigitalefolkebibliotek.dk`) as JSON
  instead of a GraphQL call against a host scraped out of the library website.
  This removes the `temp.fbi-api.dbc.dk` staging-host fallback and closes plan
  item 7.9. The endpoint needs no patron login, so it cannot be affected by
  session or token problems, and it returns richer data than the old lookup
  (publisher, page count, edition, series, shelfmark).
* `models.libraryMaterial` reads that flat record directly; the nested
  `manifestation` shape the old GraphQL call returned is no longer produced or
  expected anywhere.
* A bare faust is expanded to its `870970-basis:` record id, which is the form
  the record endpoint requires.
* Malformed API answers no longer break setup or hide items: a record response
  of `{"data": null}` is treated as a failed lookup (the loan, reservation or
  fee is kept without details), and the agency list tolerates a null payload,
  entries without an id, and non-dict entries by falling back to the scraped
  page instead of raising.
* The config flow falls back to the Biblo API's JSON agency list when the login
  page's `var libraries` script cannot be parsed, so a redesign of that page no
  longer leaves the library dropdown empty. The scraped page stays the primary
  source: it is the only one that flags `gatewayf` libraries, and it uses the
  municipality names the flow and the reverse-geocode default rely on.
* No change to which libraries can be configured, and no new Python
  dependencies.

## Version 0.9.1

**Date**: `2026-11-20`

### Changes

* Add `scripts/async_test_module.py`, a development helper that logs in to the
  library accounts and prints their current status (profile, loans, overdue
  loans, reservations, ready reservations, fees, the digital counters and which
  digital backend the site selects), either as a readable report or as JSON. It
  runs the synchronous client through `asyncio.to_thread` and needs no Home
  Assistant install.
* The script fetches every account in the secrets file in one run and guards
  against account lockouts: each user id is attempted at most once, and an id
  paired with conflicting pincodes aborts before any network call.
* No runtime change to the integration.

## Version 0.9.0

**Date**: `2026-11-19`

### Changes

* Normalize Biblo timestamps to local time so remaining loan days are correct
  around midnight and daylight-saving changes.
* Preserve optional Biblo reservation queue positions as `queue_number`.
* Expose `other_digital_loans` for types outside e-books/audiobooks, preserving
  the original material type and reconciling the digital format counts.
* Support Biblo through DBC's Biblio LMS adapter, selected automatically when
  the library website enables it. The existing digital-loans option and
  bibliotekslogin continue to apply; libraries that have not switched retain
  the Pubhub connection.
* Fetch all pages of digital loans and reservations, including available
  reservation offers. Failed requests keep the last successful results.
* Dashboard compatibility: Biblio exposes `audiobook` and `podcast` material
  types. Existing icon maps must support them or use a fallback lookup;
  see the reusable template in `BIBLO.md`.
* Expose Biblio quotas as `digital_loans_quota` on the general sensor. Shared
  allowances are not presented as independent e-book and audiobook quotas.
* See `BIBLO.md` for contract sources, limitations and manual verification.

## Version 0.8.2

**Date**: `2026-11-19`

### Changes

* Sort overdue loans by due date and fees by creation date, oldest first,
  then by title. Undated items sort last; missing titles are supported.
* Correct the internal `get_debts` flag spelling and replace a misleading
  loan-date comment (IMPROVEMENT_PLAN.md section 7.8). Sensor IDs, attributes
  and configuration keys are unchanged.


## Version 0.8.1

**Date**: `2026-10-24`

### Changes

* A failed login (wrong PIN, changed PIN, blocked account) now marks the
  sensors **unavailable** instead of silently serving stale data. Previously a
  token response without a user token went unnoticed and every sensor kept its
  old values indefinitely. The wrong-PIN path and the token-expiry re-login
  were verified against two real accounts on 2026-10-24 (see
  `IMPROVEMENT_PLAN.md` 7.1).
* One malformed loan/reservation/fee entry no longer aborts the whole refresh —
  the entry is skipped with a warning and the rest of the data still updates.
* eReolen creator names are now joined with spaces ("Jussi Adler-Olsen", was
  "JussiAdler-Olsen") and missing name parts no longer blank the whole item.
* The `renew_loans` response now includes every requested loan id — an id
  missing from the API reply shows up as `None` instead of vanishing.
* Unexpected values in the library profile (e.g. a missing SMS flag) no longer
  fail the daily profile refresh.

## Version 0.8.0

**Date**: `2026-10-11`

### Changes

* New service `bibliotek_dk.renew_loans` to renew one or more physical loans.
  Pass the `config_entry_id` and the `loan_ids` (the `renew_id` attribute on the
  loans sensor); it returns the per-loan renewal status (usable from an
  automation's `response_variable`). Invalid loan ids are rejected with a clear
  error, and a successful renewal refreshes the sensors in place.
* All sensors for an account are now grouped under a single Home Assistant
  device (one device per config entry), instead of floating loose entities.
* The debts sensor is now a proper monetary sensor: `device_class: monetary`,
  `unit_of_measurement: DKK` and `state_class: total`, so it can be graphed and
  formatted as currency.
* Added diagnostics support (Settings → Devices & Services → the integration →
  Download diagnostics). The account snapshot is included with the CPR/library
  number, PIN and account/patron names redacted.
* The loans sensor now exposes a `renew_id` attribute per loan, used by the
  renew service.
* `hacs.json` now declares a minimum Home Assistant version.
* Removed dead code: the unused `library_token` property on the transport and
  the facade.
* Internal: the DBC open-platform endpoint URLs moved into `const.py` as named
  constants.

## Version 0.7.0

**Date**: `2026-10-10`

### Changes

* Added a request timeout (10s connect / 30s read) and automatic retry with
  backoff for transient 5xx / connection errors on every API call.
* Failed requests (non-200) are now logged as warnings instead of failing
  silently and leaving stale sensor state.
* Fixed a crash in the material-details lookup when the details API returned an
  error (it raised `KeyError` on the internal cache).
* The material-details cache is now bounded so it cannot grow for the lifetime
  of the process.
* The user profile is refreshed at most once a day, so changes such as branch,
  address or notification settings propagate without a restart.
* The show/hide options (loans, debts, reservations) now actually control which
  sensors are created; previously the gates were hardcoded to `True`.
* A fee with no attached material no longer crashes the debts fetch; the amount
  is still reported.
* eReolen reservation dates are parsed as naive UTC like the physical ones, so
  the mixed reservation list sorts without timezone errors.
* Config flow: the library-list and municipality lookups moved to module-level
  helpers with proper timeouts and error handling (no more unbound-variable
  crash on timeout); fixed the copy-pasted "Dummy Garage" docstrings.
* Fixed municipality auto-detection: the DAWA endpoint
  (`api.dataforsyningen.dk`) was retired 2026-10-01 and returned HTTP 410. It now
  uses the `dawa.companydata.dk` mirror (same as the affalddk integration).
* Stopped advertising `br`/`zstd` in `Accept-Encoding`. `requests` cannot decode
  those without the optional brotli/zstandard packages, so responses (e.g. from
  the municipality lookup) came back as undecodable bytes.
* Fixed a logging crash ("not all arguments converted during string formatting")
  in the update debug message.
* Fixed a loan or reservation disappearing from the sensors when the optional
  material-details lookup failed: the item and its dates are now kept (the
  details API only adds title/cover/url), so a transient details error no longer
  hides an outstanding item.
* A failed loans/reservations/debts request now keeps the previously fetched
  items instead of replacing them with an empty list, so an API outage no longer
  shows zero loans/fees as real data. The physical and eReolen sources are
  tracked separately, so one failing API no longer discards the other's fresh
  results.
* A failed daily profile refresh now retries on the next poll instead of
  suppressing further attempts for a day.
* Retries no longer raise `RetryError` once the status retries are exhausted;
  the final response is returned so the normal status handling (warning +
  keeping the previous state) applies. A single failing API no longer aborts the
  whole update and blanks every sensor.
* CI: added the missing `voluptuous` dependency (config_flow import) and run the
  tests under both `Europe/Copenhagen` and `UTC`.
* Internal cleanup (no user-visible behavior change):
  - the `Library` god-object is split up: HTTP/login/token handling now lives in
    `transport.py` (`LibraryTransport`), the data holders in `models.py`, and
    `library_api.py` keeps `Library` as the coordinator-facing facade with the
    per-resource fetchers. The public API (including the model classes and
    `REQUEST_TIMEOUT`) is unchanged.
  - the sensor entities now share a common base class, removing ~90% duplicated
    code (all sensors behave and report identical state/attributes).
  - `libraryUser` state (loans, debts, …) is now per-instance instead of
    class-level mutable defaults, avoiding cross-instance aliasing.
  - the hardcoded `DEBUG` flag is gone; debug logging is gated by Home
    Assistant's logger config (`custom_components.bibliotek_dk: debug`).
  - all logging uses f-strings.
* Applied `ruff format` across the codebase and added `ruff format --check` to CI.
* Refreshed the README (debts/eReolen sensors, the `show_*` options, overdue
  sensor) and trimmed unrelated `.gitignore` entries.
* Added tests covering the config-flow HTTP parsing and the robustness fixes.

## Version 0.6.2

**Date**: `2026-10-10`

### Changes

* Fixed `url` and `fee_type` always being empty on loans, reservations and debts:
  - physical materials now link to `https://bibliotek.dk/work/<workId>`
  - eReolen materials link to a bibliotek.dk search on their ISBN
  - debts now expose the fee type from the fees API
* Fixed a crash when a user token was never issued (token expiry compared an
  unset attribute).
* The eReolen option is now applied at setup time instead of after the first poll.
* Config flow no longer calls `SystemExit` on HTTP errors; it reports
  "cannot connect" instead.
* The HTTP session is closed when the config entry is unloaded.
* Added a unit-test suite (no Home Assistant install required) and wired it into CI.

## Version 0.6.1

**Date**: `2025-12-17`

### Changes

* Fixed an issue where token would not be updated when expired, until HA is restarted...


---------------------------

<details>
  <summary><b>PREVIOUS CHANGES</b></summary>
