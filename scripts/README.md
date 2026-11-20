# scripts

Dev helpers for `bibliotek_dk`. They import the integration straight from
`custom_components/bibliotek_dk`, so **run them from the repo root**.

| Script | What it does |
| -- | -- |
| `async_test_module.py` | Logs in to one library account and prints the current status: profile, loans, overdue loans, reservations, ready reservations, fees, the digital (Biblo/eReolen) counters, and which digital backend the library website selects. |

## Setup

The integration modules import Home Assistant, which this repo does not
depend on. `async_test_module.py` falls back to the `homeassistant` stub in
`tests/conftest.py`, so no HA install is needed.

```bash
uv sync --group dev          # from the repo root, creates .venv
```

## async_test_module.py

```bash
# list every usable library (and the ones excluded for using gatewayf)
uv run python scripts/async_test_module.py --municipalities

# log in and print the status
uv run python scripts/async_test_module.py Kobenhavn --user-id 1234567890

# machine-readable output, e.g. to diff two runs
uv run python scripts/async_test_module.py Aarhus --json > status.json
```

The municipality name is matched case-insensitively and tolerates ASCII
spellings, so `kobenhavn` finds `København` and `Aarhu` finds `Aarhus`.

### Digital interface

The `Digital source:` line names the backend the library website selects, which
is also in the JSON as `digital_interface`:

| Value | Meaning |
| -- | -- |
| `Biblio (new adapter) <url>` | DBC's Biblio LMS adapter, with its base URL |
| `Pubhub (old eReolen) — …` | The old eReolen interface; the suffix says why the adapter was not used |
| `disabled (--no-digital)` | Digital data was skipped for this run |
| `unknown (…)` | The site's URL discovery failed, so nothing could be decided |

The adapter is selected only when the site advertises **both**
`data-use-biblio-adapter-config=1` and a `data-biblio-base-url` — the URL is
also published before rollout, so its presence alone means nothing. That is why
`Pubhub` can report `adapter flag is 1 but no base URL`, and why the flag is
shown next to the base URL in the JSON.

### Credentials

Where credentials come from, first source wins:

1. `--user-id` / `--pincode`
2. a gitignored secrets file (`user_id pincode` per line) — `--secrets`, default
   `secrets` relative to the repo root. **Every usable line is fetched**, so one
   run reports every account in the file; with `--user-id` given, only that
   user's own line(s) are used
3. `BIBLIOTEK_USER_ID` / `BIBLIOTEK_PINCODE` environment variables
4. an interactive prompt (`getpass` for the pincode)

Non-digits are stripped from both, like the config flow does. Blank lines and
`#` comments are ignored; a line without both fields is skipped with a warning.
A rejected account does not stop the run — the rest are still fetched, the
failures are summarised on stderr, and the exit code is `1` only if *no* account
could be fetched.

### Output shape

With one account the report is a single block. With several, each block starts
with an `Account:` line naming the source line, and `--json` returns a list of
objects, each with a `label` field. The single-account JSON is unchanged (an
object, not a list), so existing pipelines keep working.

### Lockout safety

Repeated wrong pincodes can lock a real library account (borchk/Cicero locks
after a handful of failures), so the script refuses to make that possible:

- **One attempt per user id.** A rejected id is never retried in the same run,
  even if the file lists it more than once.
- **Conflicting pincodes abort the run.** If one id appears with two different
  pincodes, the script exits `2` *before* any network call and names the lines.

If a login is rejected, do not re-run with another guess at the pincode — check
the account on the library's own website first, and let staff or the
"forgot pincode" flow clear a lock.

### Options

| Flag | Effect |
| -- | -- |
| `--no-digital` | Skip the digital loans/reservations (faster, physical only) |
| `--json` | Print the whole status as JSON instead of the readable report |
| `-v`, `--verbose` | Show the client's debug log, including every HTTP request |

Exit codes: `0` success, `1` login rejected, `2` bad input or a failed request.

The client is synchronous (`requests`), so the script wraps each call in
`asyncio.to_thread` — the same way `sensor.py` uses
`hass.async_add_executor_job`. The session is logged out and closed on exit.

Real credentials are only ever read from the flags, the environment or the
gitignored secrets file; never commit them.
