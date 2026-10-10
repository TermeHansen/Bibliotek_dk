# Changelog for Bibliotek DK Home Assistant Integration

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
