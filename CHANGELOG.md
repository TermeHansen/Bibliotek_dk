# Changelog for Bibliotek DK Home Assistant Integration

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
