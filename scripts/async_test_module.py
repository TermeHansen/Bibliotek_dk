#!/usr/bin/env python3
"""Live probe of one library account, for developing the integration.

Logs in with a user id and a municipality and prints the current status: the
patron profile, loans, overdue loans, reservations, ready reservations and
fees, plus the digital (Biblo/eReolen) counters.

The client is synchronous (``requests``), so every blocking call runs in a
worker thread via ``asyncio.to_thread`` — the same way ``sensor.py`` does it
with ``hass.async_add_executor_job``.

Credentials come from ``--user-id``/``--pincode``, else a gitignored secrets
file (``--secrets``, default ``secrets`` relative to the repo root, one
``user_id pincode`` per line — every usable line is fetched, so one run reports
every account in the file), else the ``BIBLIOTEK_USER_ID``/
``BIBLIOTEK_PINCODE`` environment variables, else a prompt.

Run from the repo root:

    uv run python scripts/async_test_module.py --municipalities
    uv run python scripts/async_test_module.py Kobenhavn --user-id 1234567890
    uv run python scripts/async_test_module.py Aarhus --json
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import getpass
import json
import logging
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The integration modules import Home Assistant. tests/conftest.py stubs the
# small surface they touch, so the script runs without a HA install.
try:
    import homeassistant  # noqa: F401
except ModuleNotFoundError:
    import tests.conftest  # noqa: F401

from requests.exceptions import RequestException  # noqa: E402

from custom_components.bibliotek_dk.config_flow import (  # noqa: E402
    CannotConnect,
    refreshLibraries,
)
from custom_components.bibliotek_dk.const import (  # noqa: E402
    CONF_AGENCY,
    CONF_HOST,
)
from custom_components.bibliotek_dk.library_api import (  # noqa: E402
    Library,
    LibraryAuthError,
)

_LOGGER = logging.getLogger(__name__)

SEPARATOR = "=" * 60
# Default credential file, relative to the repo root. Kept out of the argparse
# default so --help never prints an absolute local path.
SECRETS_FILE = Path("secrets")

# Fold Danish characters so an ASCII spelling still finds the library.
_FOLD = str.maketrans(
    {"æ": "ae", "ø": "o", "å": "a", "ö": "o", "ä": "a", "ü": "u", "é": "e"}
)


def fold(text: str) -> str:
    """Lower-case and strip diacritics; treat the Danish 'aa' spelling as one a."""
    return text.lower().translate(_FOLD).replace("aa", "a")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Log in to a library account and print its current status.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "municipality", nargs="?", help="library/municipality name, e.g. Aarhus"
    )
    parser.add_argument(
        "--municipalities", action="store_true", help="list usable libraries and exit"
    )
    parser.add_argument("--user-id", help="CPR number / loan number")
    parser.add_argument("--pincode", help="pincode")
    parser.add_argument(
        "--secrets",
        type=Path,
        default=SECRETS_FILE,
        help="file with 'user_id pincode' lines, used when the flags are omitted",
    )
    parser.add_argument(
        "--no-digital",
        action="store_true",
        help="skip the digital (Biblo/eReolen) loans and reservations",
    )
    parser.add_argument("--json", action="store_true", help="print the status as JSON")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="show the client's debug log"
    )
    return parser.parse_args()


def match_municipality(name: str, libraries: dict) -> str | None:
    """Resolve a user-typed name against the scraped library list.

    Exact match wins; otherwise a unique prefix or substring match is accepted,
    so ``aarhus`` and ``Aarhu`` both find ``Aarhus``, and ``kobenhavn`` finds
    ``København``.
    """
    wanted = fold(name)
    for key in libraries:
        if fold(key) == wanted:
            return key
    for matches in (
        [key for key in libraries if fold(key).startswith(wanted)],
        [key for key in libraries if wanted in fold(key)],
    ):
        if len(matches) == 1:
            return matches[0]
    return None


def digits(value: str) -> str:
    """Strip everything but digits, like the config flow does before login."""
    return re.sub(r"\D", "", value)


def read_secrets(path: Path) -> list[tuple[int, list[str]]]:
    """Return (line number, fields) for each usable line of a secrets file."""
    entries = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        fields = line.split()
        if not fields or line.lstrip().startswith("#"):
            continue
        if len(fields) < 2:
            _LOGGER.warning(f"Ignoring secrets line {number}: need 'user_id pincode'")
            continue
        entries.append((number, fields))
    return entries


def credential_candidates(args: argparse.Namespace) -> list[tuple[str, str, str]]:
    """Return ``(user_id, pincode, label)`` candidates, in the order to fetch them.

    Every usable line of the secrets file is a candidate: one run reports every
    account, and a rejected one does not stop the rest. Precedence: the flags
    win outright, then the secrets file, then the environment, then a prompt.
    A ``--pincode`` on its own overrides the stored PIN for the candidates
    rather than being ignored, so a corrected PIN is the one actually sent.
    """
    # A relative --secrets path is taken from the repo root, so the default
    # works regardless of the working directory.
    path = args.secrets if args.secrets.is_absolute() else ROOT / args.secrets
    entries = read_secrets(path) if path.is_file() else []

    if args.user_id is not None and args.pincode is not None:
        return [(digits(args.user_id), digits(args.pincode), "flags")]

    if args.user_id is not None:
        # Only that user's own line(s): never pair an id with another's pincode.
        wanted = digits(args.user_id)
        entries = [entry for entry in entries if digits(entry[1][0]) == wanted]

    # A PIN given alone overrides the stored one for every candidate. Without
    # this the supplied value was silently ignored, so a developer correcting a
    # stale PIN still sent the saved one — and a rejected PIN can lock the
    # account. The user id is never overridden, so no id is paired with a PIN
    # that was not meant for it.
    override = digits(args.pincode) if args.pincode is not None else None

    candidates = [
        (
            digits(fields[0]),
            override or digits(fields[1]),
            f"line {number} of {args.secrets}",
        )
        for number, fields in entries
    ]
    if candidates:
        return candidates

    user_id = args.user_id or os.environ.get("BIBLIOTEK_USER_ID")
    pincode = args.pincode or os.environ.get("BIBLIOTEK_PINCODE")
    if user_id and pincode:
        return [(digits(user_id), digits(pincode), "environment")]
    if not user_id:
        user_id = input("User id (CPR / loan number): ")
    if not pincode:
        pincode = getpass.getpass("Pincode: ")
    if not user_id or not pincode:
        return []
    return [(digits(user_id), digits(pincode), "prompt")]


async def close_library(library: Library) -> None:
    """End the session server-side and drop the HTTP session, as unload does.

    Never raises. ``logout()`` is a network call and this runs in a ``finally``,
    so an exception here would replace the account's result — masking a login
    error or a good snapshot — and stop the remaining accounts in the run. The
    local session is closed even when the server-side logout fails.
    """
    try:
        await asyncio.to_thread(library.logout)
    except Exception as err:
        _LOGGER.warning(f"Ignoring logout error: {err}")
    finally:
        await asyncio.to_thread(library.session.close)


def conflicting_pincodes(
    candidates: list[tuple[str, str, str]],
) -> dict[str, list[str]]:
    """Return ``{user_id: [labels]}`` for ids paired with more than one pincode.

    A wrong pincode can lock a real patron account out (borchk/Cicero locks
    after a handful of failures), so an id must never be tried with two
    different pincodes in one run — that is the one thing this script could do
    to cause a lockout by itself.
    """
    seen: dict[str, dict[str, list[str]]] = {}
    for user_id, pincode, label in candidates:
        seen.setdefault(user_id, {}).setdefault(pincode, []).append(label)
    return {
        user_id: [label for labels in by_pin.values() for label in labels]
        for user_id, by_pin in seen.items()
        if len(by_pin) > 1
    }


async def fetch_account(
    user_id: str, pincode: str, config: dict, name: str, use_digital: bool
) -> tuple[dict | None, str]:
    """Log in and snapshot one account. Returns ``(status, error)``.

    One login attempt only: a rejected id is never retried, because repeated
    failures are what lock a patron account.
    """
    library = Library(
        user_id,
        pincode,
        config[CONF_HOST],
        config[CONF_AGENCY],
        libraryName=name,
        use_eReolen=use_digital,
    )
    try:
        # Login is one cheap request; a rejected account should not pay for a
        # full update() of loans, reservations and fees.
        if not await asyncio.to_thread(library.login):
            return None, "login rejected"
        await asyncio.to_thread(library.update)
    except LibraryAuthError as err:
        return None, f"login failed: {err}"
    except RequestException as err:
        return None, f"request failed: {err}"
    finally:
        await close_library(library)
    return status(library), ""


def digital_interface(library: Library) -> dict:
    """Report which digital backend the library website selects.

    ``Library.update`` builds a ``BiblioClient`` only when the site advertises
    ``data-use-biblio-adapter-config=1`` *and* a ``data-biblio-base-url``; the
    URL alone is advertised before rollout, so it is not enough. The raw flags
    are reported too, to tell "not rolled out yet" from "flag on, URL missing".
    """
    try:
        # Already fetched during update() when digital is enabled; with
        # --no-digital this costs one extra request.
        config = library.urls
    except RequestException as err:
        return {"backend": "unknown", "detail": f"URL discovery failed: {err}"}

    biblio_url = config.get("data-biblio-base-url")
    flag = config.get("data-use-biblio-adapter-config")
    if not library.use_eReolen:
        backend = "disabled"
    elif library._biblio is not None:
        backend = "biblio"
    else:
        backend = "pubhub"
    return {
        "backend": backend,
        "biblio_adapter_flag": flag,
        "biblio_base_url": biblio_url,
        "adapter_rolled_out": flag == "1" and bool(biblio_url),
    }


def iso(value):
    """Serialise a datetime for the JSON output."""
    return value.isoformat() if isinstance(value, datetime) else value


def item_data(item) -> dict:
    """Everything a material-ish object carries, as plain data."""
    return {
        "id": item.id,
        "title": item.title,
        "creators": item.creators,
        "type": item.type,
        "url": item.url,
        "cover": item.coverUrl,
    }


def status(library: Library) -> dict:
    """Snapshot the client's state as plain data."""
    user = library.user
    return {
        "library": library.libraryName,
        "host": library.host,
        "digital_interface": digital_interface(library),
        "profile": {
            "name": user.name,
            "address": user.address,
            "phone": user.phone,
            "phone_notifications": user.phoneNotify,
            "mail": user.mail,
            "mail_notifications": user.mailNotify,
            "pickup_library": user.pickupLibrary,
        },
        "digital": {
            "ebooks": user.eBooks,
            "ebooks_quota": user.eBooksQuota,
            "audiobooks": user.audioBooks,
            "audiobooks_quota": user.audioBooksQuota,
            "other_digital_loans": user.otherDigitalLoans,
            "digital_loans_quota": user.digitalLoansQuota,
        },
        "loans": [
            {
                **item_data(loan),
                "loan_date": iso(loan.loanDate),
                "expire_date": iso(loan.expireDate),
                "renewable": loan.renewAble,
                "renew_id": loan.renewId,
            }
            for loan in user.loans
        ],
        "loans_overdue": [
            {
                **item_data(loan),
                "loan_date": iso(loan.loanDate),
                "expire_date": iso(loan.expireDate),
            }
            for loan in user.loansOverdue
        ],
        "reservations": [
            {
                **item_data(reservation),
                "queue_number": reservation.queueNumber,
                "created_date": iso(reservation.createdDate),
                "expire_date": iso(reservation.expireDate),
                "pickup_library": reservation.pickupLibrary,
            }
            for reservation in user.reservations
        ],
        "reservations_ready": [
            {
                **item_data(reservation),
                "reservation_number": reservation.reservationNumber,
                "created_date": iso(reservation.createdDate),
                "pickup_date": iso(reservation.pickupDate),
                "pickup_library": reservation.pickupLibrary,
            }
            for reservation in user.reservationsReady
        ],
        "debts": [
            {
                **item_data(debt),
                "fee_date": iso(debt.feeDate),
                "fee_due_date": iso(debt.feeDueDate),
                "fee_type": debt.feeType,
                "fee_amount": debt.feeAmount,
            }
            for debt in user.debts
        ],
        "debts_amount": user.debtsAmount,
    }


def describe_interface(interface: dict) -> str:
    """One line naming the digital backend and the evidence for it."""
    backend = interface["backend"]
    if backend == "biblio":
        return f"Biblio (new adapter) {interface['biblio_base_url']}"
    if backend == "disabled":
        return "disabled (--no-digital)"
    if backend == "unknown":
        return f"unknown ({interface.get('detail')})"
    # Pubhub is the old eReolen interface; say why the adapter was not used.
    flag = interface.get("biblio_adapter_flag")
    if flag == "1":
        return "Pubhub (old eReolen) — adapter flag is 1 but no base URL"
    if flag is None:
        return "Pubhub (old eReolen) — site advertises no adapter flag"
    return f"Pubhub (old eReolen) — adapter flag is {flag}"


def print_status(data: dict, label: str = "") -> None:
    """Print the snapshot for a human reader; ``label`` prefixes a multi-account run."""
    profile = data["profile"]
    print(SEPARATOR)
    if label:
        print(f"Account:        {label}")
    print(f"Library:        {data['library']}  ({data['host']})")
    print(f"User:           {profile['name']}")
    print(f"Address:        {(profile['address'] or '').replace(chr(10), ', ')}")
    print(
        f"Phone:          {profile['phone']} (notify: {profile['phone_notifications']})"
    )
    print(
        f"Mail:           {profile['mail']} (notify: {profile['mail_notifications']})"
    )
    print(f"Pickup library: {profile['pickup_library']}")
    interface = data["digital_interface"]
    print(f"Digital source: {describe_interface(interface)}")
    digital = data["digital"]
    print(
        f"Digital:        ebooks {digital['ebooks']}/{digital['ebooks_quota']}, "
        f"audiobooks {digital['audiobooks']}/{digital['audiobooks_quota']}, "
        f"other {digital['other_digital_loans']}"
    )
    print(
        f"Totals:         loans {len(data['loans'])}, "
        f"overdue {len(data['loans_overdue'])}, "
        f"reservations {len(data['reservations'])}, "
        f"ready {len(data['reservations_ready'])}, "
        f"fees {len(data['debts'])} ({data['debts_amount']} DKK)"
    )

    for loan in data["loans"]:
        print("")
        print(SEPARATOR)
        print("Loan:")
        print(f"  Title:      {loan['title']}")
        print(f"  Creators:   {loan['creators']}")
        print(f"  Type:       {loan['type']}")
        print(f"  Loan date:  {loan['loan_date']}")
        print(f"  Due:        {loan['expire_date']}")
        print(f"  Renewable:  {loan['renewable']} (renew_id: {loan['renew_id']})")
        print(f"  URL:        {loan['url']}")

    for loan in data["loans_overdue"]:
        print("")
        print(SEPARATOR)
        print("Overdue loan:")
        print(f"  Title:      {loan['title']}")
        print(f"  Creators:   {loan['creators']}")
        print(f"  Type:       {loan['type']}")
        print(f"  Loan date:  {loan['loan_date']}")
        print(f"  Due:        {loan['expire_date']}")
        print(f"  URL:        {loan['url']}")

    for reservation in data["reservations"]:
        print("")
        print(SEPARATOR)
        print("Reservation:")
        print(f"  Title:      {reservation['title']}")
        print(f"  Creators:   {reservation['creators']}")
        print(f"  Type:       {reservation['type']}")
        print(f"  Queue:      {reservation['queue_number']}")
        print(f"  Created:    {reservation['created_date']}")
        print(f"  Expires:    {reservation['expire_date']}")
        print(f"  Pickup:     {reservation['pickup_library']}")
        print(f"  URL:        {reservation['url']}")

    for reservation in data["reservations_ready"]:
        print("")
        print(SEPARATOR)
        print("Reservation ready:")
        print(f"  Title:      {reservation['title']}")
        print(f"  Creators:   {reservation['creators']}")
        print(f"  Type:       {reservation['type']}")
        print(f"  Number:     {reservation['reservation_number']}")
        print(f"  Created:    {reservation['created_date']}")
        print(f"  Pick up by: {reservation['pickup_date']}")
        print(f"  Pickup:     {reservation['pickup_library']}")
        print(f"  URL:        {reservation['url']}")

    for debt in data["debts"]:
        print("")
        print(SEPARATOR)
        print("Fee:")
        print(f"  Title:      {debt['title']}")
        print(f"  Type:       {debt['fee_type']} ({debt['type']})")
        print(f"  Created:    {debt['fee_date']}")
        print(f"  Due:        {debt['fee_due_date']}")
        print(f"  Amount:     {debt['fee_amount']} DKK")

    print("")
    print(SEPARATOR)


async def run(args: argparse.Namespace) -> int:
    """Do the work; return the process exit code."""
    try:
        libraries = await asyncio.to_thread(refreshLibraries)
    except CannotConnect:
        print("Could not fetch the library list (see the log above).", file=sys.stderr)
        return 2

    if args.municipalities:
        for name in sorted(libraries):
            print(name)
        return 0

    name = match_municipality(args.municipality, libraries)
    if name is None:
        candidates = [
            key for key in sorted(libraries) if fold(args.municipality) in fold(key)
        ]
        print(f"Unknown library '{args.municipality}'.", file=sys.stderr)
        if candidates:
            print(f"Did you mean: {', '.join(candidates)}", file=sys.stderr)
        else:
            print("Run with --municipalities to list them.", file=sys.stderr)
        return 2

    candidates = credential_candidates(args)
    if not candidates:
        print("Both a user id and a pincode are required.", file=sys.stderr)
        return 2

    conflicts = conflicting_pincodes(candidates)
    if conflicts:
        for user_id, labels in conflicts.items():
            print(
                f"Refusing to run: id {user_id} is paired with different pincodes "
                f"({', '.join(labels)}).",
                file=sys.stderr,
            )
        print(
            "Repeated wrong pincodes can lock a real library account. Fix the "
            "file so each id has one pincode.",
            file=sys.stderr,
        )
        return 2

    started = time.time()
    results, failures = [], []
    seen_ids = set()
    for user_id, pincode, label in candidates:
        # One attempt per id, even if the file lists an id on several lines.
        if user_id in seen_ids:
            print(f"Skipping {label}: id {user_id} was already tried.", file=sys.stderr)
            continue
        seen_ids.add(user_id)
        print(f"Fetching {label} (id {user_id})...", file=sys.stderr)
        data, error = await fetch_account(
            user_id,
            pincode,
            libraries[name],
            name,
            not args.no_digital,
        )
        if data is None:
            failures.append((label, error))
            print(f"  {error}; not retrying this id.", file=sys.stderr)
            continue
        results.append((label, data))

    if args.json:
        payload = (
            results[0][1]
            if len(results) == 1
            else [{"label": label, **data} for label, data in results]
        )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for label, data in results:
            print_status(data, label if len(candidates) > 1 else "")

    for label, error in failures:
        print(f"Failed: {label} ({error})", file=sys.stderr)
    print(
        f"{len(results)} of {len(candidates)} account(s) fetched in "
        f"{time.time() - started:.2f} seconds",
        file=sys.stderr,
    )
    if not results:
        return 1
    return 0


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if not args.municipalities and not args.municipality:
        print("A municipality is required (or pass --municipalities).", file=sys.stderr)
        return 2
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
