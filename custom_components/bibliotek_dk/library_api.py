"""Library client facade.

``Library`` is the object Home Assistant stores per config entry. It keeps the
per-account state (profile, material-details cache, resource URLs) and the
per-resource fetchers. HTTP, login and tokens live in
:class:`~.transport.LibraryTransport`; the data holders live in :mod:`.models`.
"""

from __future__ import annotations

from dateutil import parser
from datetime import timedelta, datetime
import logging
import re

from .const import (
    URL_BRANCH_GRAPHQL,
    URL_FEES,
    URL_LOANS,
    URL_LOANS_RENEW,
    URL_PATRON,
    URL_PUBHUB_BASE,
    URL_RESERVATIONS,
    branch_query,
    details_query,
)
from .models import (
    libraryDebt,
    libraryLoan,
    libraryMaterial,
    libraryReservation,
    libraryReservationReady,
    libraryUser,
)
from .transport import REQUEST_TIMEOUT, LibraryTransport

# Maximum number of entries kept in the material-details cache.
DETAILS_CACHE_MAX = 500


_LOGGER = logging.getLogger(__name__)


class LibraryAuthError(Exception):
    """Raised when the library login did not produce a user token."""


# Re-exported so existing imports (``from .library_api import libraryLoan``)
# and the public ``REQUEST_TIMEOUT`` constant keep working.
__all__ = [
    "DETAILS_CACHE_MAX",
    "REQUEST_TIMEOUT",
    "Library",
    "LibraryAuthError",
    "libraryDebt",
    "libraryLoan",
    "libraryMaterial",
    "libraryReservation",
    "libraryReservationReady",
    "libraryUser",
]


class Library:
    def __init__(
        self,
        userId: str,
        pincode: str,
        host: str,
        agency: str,
        libraryName=None,
        use_eReolen: bool = True,
    ) -> None:

        self.agency = agency
        self.libraryName = None
        self.icon = None
        self.user = libraryUser(userId=userId, pincode=pincode)
        self.user.date = self.user.userId[:-4]
        self.municipality = libraryName
        self.use_eReolen = use_eReolen
        self.get_loans = True
        self.get_reservations = True
        self.get_depts = True

        self._user_info_fetched = None
        self.branches = {}
        self._urls = {}
        self._details = {}
        # Last successful per-source lists. A failed request keeps its source's
        # previous items rather than publishing an empty list; the two sources
        # (physical DBC and eReolen) are tracked separately so one failing API
        # does not discard the other's fresh results.
        self._loans = {"physical": [], "ereolen": []}
        self._loans_overdue = {"physical": [], "ereolen": []}
        self._reservations = {"physical": [], "ereolen": []}
        self._reservations_ready = {"physical": [], "ereolen": []}

        self._transport = LibraryTransport(host, self.user)

    # The update function is called from the coordinator from Home Assistant
    def update(self):
        self._debug(
            f"Updating {self.use_eReolen}, {self.get_loans}, "
            f"{self.get_reservations}, {self.get_depts}"
        )

        # A missing user token means login failed (bad credentials, blocked
        # account, ...). Fail loudly so the coordinator marks the entities
        # unavailable instead of silently serving stale data (plan 7.1). The
        # user_token property performs the login/refresh itself when needed.
        if not self._transport.user_token:
            raise LibraryAuthError(
                f"Login failed for {self.user.date} (check user id and pincode)"
            )

        # Fetch the profile on the first poll and then at most once a day, so
        # changes such as branch, address or notification settings propagate.
        now = datetime.now()
        first = not self.user.name or self._user_info_fetched is None
        stale = (
            self._user_info_fetched is not None
            and now - self._user_info_fetched > timedelta(days=1)
        )
        if first or stale:
            if first:
                self._branchName(self.agency)
            # Only advance the timestamp on success, so a failed refresh (e.g.
            # HTTP 429) is retried on the next poll instead of a day later.
            if self.fetchUserInfo():
                self._user_info_fetched = now

        # Fetch the states of the user
        if self.get_loans:
            self.fetchLoans()
        if self.get_reservations:
            self.fetchReservations()
        if self.get_depts:
            self.fetchDebts()

        # Sort the lists
        self.sortLists()
        return True

    # Transport passthroughs: keep the historical attribute surface so callers
    # (and the config flow / unload hook) do not need to know about the split.
    @property
    def host(self):
        return self._transport.host

    @property
    def session(self):
        return self._transport.session

    @session.setter
    def session(self, session):
        self._transport.session = session

    def _debug(self, msg):
        self._transport._debug(msg)

    def _get(self, url, **kwargs):
        return self._transport._get(url, **kwargs)

    def _post(self, url, **kwargs):
        return self._transport._post(url, **kwargs)

    @property
    def json_header(self):
        return self._transport.json_header

    @property
    def user_token(self):
        return self._transport.user_token

    def login(self):
        return self._transport.login()

    def logout(self):
        self._transport.logout()

    # PRIVATE BEGIN ####
    def sortLists(self):
        # A loan/reservation kept without details has title None; fall back to
        # "" so a sort key never mixes None with str (which would raise
        # TypeError and abort the whole refresh).
        # Sort the loans by expireDate and the Title
        self.user.loans.sort(
            key=lambda obj: (obj.expireDate is None, obj.expireDate, obj.title or "")
        )
        # Sort the reservations
        self.user.reservations.sort(
            key=lambda obj: (
                obj.queueNumber is None,
                obj.queueNumber,
                obj.createdDate is None,
                obj.createdDate,
                obj.title or "",
            )
        )
        # Sort the reservations
        self.user.reservationsReady.sort(
            key=lambda obj: (obj.pickupDate is None, obj.pickupDate, obj.title or "")
        )

    def _branchName(self, id):
        id = str(id).split("-")[-1]
        if id in self.branches:
            return self.branches[id]

        params = {
            "query": branch_query,
            "variables": {"language": "DA", "limit": 50, "q": id},
        }
        header = {"Accept": "*/*"}
        res = self._post(URL_BRANCH_GRAPHQL, headers=header, json=params)
        if res.status_code == 200:
            data = res.json()["data"]["branches"]
            for branch in data["result"]:
                self.branches[branch["branchId"]] = branch["name"]
        else:
            _LOGGER.warning(
                f"({self.user.date}) Error looking up branch '{id}' ({res.status_code})"
            )
        return self.branches.get(id, id)

    def _getDetails(self, faust):
        if faust in self._details:
            return self._details[faust]
        data = {}
        params = {"query": details_query, "variables": {"faust": faust}}
        url = self.urls.get(
            "data-fbi-global-base-url",
            "https://temp.fbi-api.dbc.dk/next-present/graphql",
        )
        res = self._post(url, headers=self.json_header, json=params)
        if res.status_code == 200:
            data = res.json()["data"]
            # Bound the cache so it does not grow for the process lifetime.
            if len(self._details) >= DETAILS_CACHE_MAX:
                self._details.clear()
            self._details[faust] = data
        else:
            _LOGGER.warning(
                f"({self.user.date}) Error getting details for material: '{faust}' ({res.status_code})"
            )
        return data

    @property
    def urls(self):
        if not self._urls:
            res = self._get(f"{self.host}/user/me/loans")
            if res.status_code == 200:
                self._urls = {
                    m[0]: m[1]
                    for m in re.findall(
                        r'(data-[a-zA-Z0-9\-\_]+-url)="([^"]*)"', res.text
                    )
                }
            else:
                _LOGGER.warning(
                    f"({self.user.date}) Error fetching API urls ({res.status_code})"
                )
        return self._urls

    # PRIVATE END  ####

    # Get information on the user. Returns True when the profile was updated.
    def fetchUserInfo(self):
        # Fetch the user profile page
        res = self._get(URL_PATRON, headers=self.json_header)
        if res.status_code == 200:
            try:
                data = res.json()["patron"]

                self.user.name = data["name"]
                self.user.address = f"{data['address']['street']}\n{data['address']['postalCode']} {data['address']['city']}"
                self.user.phone = data["phoneNumber"]
                self.user.phoneNotify = int(data["receiveSms"])
                self.user.mail = data["emailAddress"]
                self.user.mailNotify = int(data["receiveEmail"])
                self.user.pickupLibrary = self._branchName(
                    data["preferredPickupBranch"]
                )
                self.libraryName = self._branchName(data["preferredPickupBranch"])
                return True
            except (AttributeError, KeyError, TypeError, ValueError) as err:
                _LOGGER.error(f"Error getting user info {self.user.date}. Error: {err}")
        else:
            _LOGGER.warning(
                f"({self.user.date}) Error fetching user info ({res.status_code})"
            )
        return False

    # Get the loans with all possible details
    def fetchLoans(self):
        # Physical books
        res = self._get(URL_LOANS, headers=self.json_header)
        if res.status_code == 200:
            physical = []
            physical_overdue = []
            for material in res.json():
                try:
                    id = material["loanDetails"]["recordId"]
                    # A failed details lookup returns {}; keep the loan anyway (the
                    # dates below come from the loans API) so a transient details
                    # error does not hide an outstanding loan.
                    obj = libraryLoan(self._getDetails(id))

                    # Renewable
                    obj.renewId = material["loanDetails"]["loanId"]
                    obj.renewAble = material["isRenewable"]
                    obj.loanDate = parser.parse(
                        material["loanDetails"]["loanDate"], ignoretz=True
                    )
                    obj.expireDate = parser.parse(
                        material["loanDetails"]["dueDate"], ignoretz=True
                    ) + timedelta(hours=23, minutes=59)
                    obj.id = material["loanDetails"]["materialItemNumber"]
                except Exception as err:
                    # One malformed entry must not abort the whole refresh.
                    _LOGGER.warning(
                        f"({self.user.date}) Skipping malformed loan entry: {err}"
                    )
                    continue
                if obj.expireDate < datetime.now():
                    physical_overdue.append(obj)
                else:
                    physical.append(obj)
            self._loans["physical"] = physical
            self._loans_overdue["physical"] = physical_overdue
        else:
            _LOGGER.warning(
                f"({self.user.date}) Error fetching loans ({res.status_code})"
            )
        # Ebooks
        if self.use_eReolen:
            res = self._get(f"{URL_PUBHUB_BASE}/user/loans", headers=self.json_header)
            if res.status_code == 200:
                edata = res.json()

                try:
                    self.user.eBooks = edata["userData"]["totalEbookLoans"]
                    self.user.eBooksQuota = edata["libraryData"][
                        "maxConcurrentEbookLoansPerBorrower"
                    ]
                    self.user.audioBooks = edata["userData"]["totalAudioLoans"]
                    self.user.audioBooksQuota = edata["libraryData"][
                        "maxConcurrentAudiobookLoansPerBorrower"
                    ]
                except (KeyError, TypeError) as err:
                    _LOGGER.warning(
                        f"({self.user.date}) Unexpected eReolen loans payload: {err}"
                    )

                ereloans = []
                for material in edata.get("loans") or []:
                    try:
                        id = material["libraryBook"]["identifier"]
                    except (KeyError, TypeError) as err:
                        _LOGGER.warning(
                            f"({self.user.date}) Skipping malformed eReolen loan entry: {err}"
                        )
                        continue
                    res2 = self._get(
                        f"{URL_PUBHUB_BASE}/products/{id}", headers=self.json_header
                    )
                    if res2.status_code != 200:
                        _LOGGER.warning(
                            f"({self.user.date}) Error fetching eReolen product '{id}' ({res2.status_code})"
                        )
                        continue
                    try:
                        obj = libraryLoan(res2.json()["product"])
                        obj.id = id
                        obj.loanDate = parser.parse(
                            material["orderDateUtc"], ignoretz=True
                        )
                        obj.expireDate = parser.parse(
                            material["loanExpireDateUtc"], ignoretz=True
                        )
                    except Exception as err:
                        _LOGGER.warning(
                            f"({self.user.date}) Skipping malformed eReolen product '{id}': {err}"
                        )
                        continue
                    ereloans.append(obj)
                self._loans["ereolen"] = ereloans
            else:
                _LOGGER.warning(
                    f"({self.user.date}) Error fetching eReolen loans ({res.status_code})"
                )
        else:
            self.user.eBooks = 0
            self.user.eBooksQuota = 0
            self.user.audioBooks = 0
            self.user.audioBooksQuota = 0
            self._loans["ereolen"] = []
            self._loans_overdue["ereolen"] = []

        # Merge the last successful results from each source; a failed request
        # keeps its source's previous items, so an outage shows stale data
        # rather than zero.
        self.user.loans = self._loans["physical"] + self._loans["ereolen"]
        self.user.loansOverdue = (
            self._loans_overdue["physical"] + self._loans_overdue["ereolen"]
        )

    # Renew one or more physical loans. ``loan_ids`` are the API's ``loanId``
    # values, exposed to users as ``renew_id`` on the loan sensor attributes.
    # Returns a dict mapping each requested id to its renewal status: "renewed"
    # on success, otherwise the API's denial reason (e.g. "deniedReserved").
    def renewLoans(self, loan_ids):
        # The endpoint expects a bare JSON array of loan ids, e.g. ``[123]``.
        requested = [int(loan_id) for loan_id in loan_ids]
        res = self._post(
            URL_LOANS_RENEW,
            headers=self.json_header,
            json=requested,
        )
        if res.status_code != 200:
            # Errors come back as {"errorCode": ..., "message": ...}, but a
            # gateway error may return a non-JSON body instead.
            try:
                body = res.json()
            except ValueError:
                body = None
            detail = body.get("errorCode") if isinstance(body, dict) else None
            _LOGGER.warning(
                f"({self.user.date}) Error renewing loans ({res.status_code}"
                f"{f', {detail}' if detail else ''})"
            )
            return {}
        # Seed with every requested id so an id missing from the reply shows
        # up as None ("no answer") instead of silently vanishing.
        status = dict.fromkeys(requested)
        for item in res.json():
            loan_id = (item.get("loanDetails") or {}).get("loanId")
            renewal = item.get("renewalStatus") or []
            status[loan_id] = renewal[0] if renewal else None
        return status

    # Get the current reservations
    def fetchReservations(self):
        # Physical books
        res = self._get(URL_RESERVATIONS, headers=self.json_header)
        if res.status_code == 200:
            materials = {
                item["transactionId"]: item for item in res.json()
            }  # make sure only to take last if more than one item with same transaction
            physical = []
            physical_ready = []
            for material in materials.values():
                try:
                    id = material["recordId"]
                    # A failed details lookup returns {}; keep the reservation anyway
                    # (its dates and queue number come from the reservations API) so
                    # a transient details error does not hide an outstanding item.
                    data = self._getDetails(id)
                    if material["state"] == "readyForPickup":
                        obj = libraryReservationReady(data)
                    else:
                        obj = libraryReservation(data)

                    # Details
                    obj.id = id
                    obj.createdDate = parser.parse(
                        material["dateOfReservation"], ignoretz=True
                    )
                    obj.pickupLibrary = self._branchName(material["pickupBranch"])
                except Exception as err:
                    # One malformed entry must not abort the whole refresh.
                    _LOGGER.warning(
                        f"({self.user.date}) Skipping malformed reservation entry: {err}"
                    )
                    continue
                if material["state"] == "readyForPickup":
                    obj.reservationNumber = material["pickupNumber"]
                    obj.pickupDate = parser.parse(
                        material["pickupDeadline"], ignoretz=True
                    )
                    physical_ready.append(obj)
                else:
                    obj.expireDate = parser.parse(material["expiryDate"], ignoretz=True)
                    obj.queueNumber = material["numberInQueue"]
                    physical.append(obj)
            self._reservations["physical"] = physical
            self._reservations_ready["physical"] = physical_ready
        else:
            _LOGGER.warning(
                f"({self.user.date}) Error fetching reservations ({res.status_code})"
            )

        # eReolen
        if self.use_eReolen:
            res = self._get(
                f"{URL_PUBHUB_BASE}/user/reservations", headers=self.json_header
            )
            if res.status_code == 200:
                edata = res.json()
                erereservations = []
                for material in edata.get("reservations") or []:
                    try:
                        id = material["identifier"]
                    except (KeyError, TypeError) as err:
                        _LOGGER.warning(
                            f"({self.user.date}) Skipping malformed eReolen reservation entry: {err}"
                        )
                        continue
                    res2 = self._get(
                        f"{URL_PUBHUB_BASE}/products/{id}", headers=self.json_header
                    )
                    if res2.status_code != 200:
                        _LOGGER.warning(
                            f"({self.user.date}) Error fetching eReolen product '{id}' ({res2.status_code})"
                        )
                        continue
                    try:
                        data = res2.json()["product"]

                        obj = libraryReservation(data)
                        obj.id = id

                        # Parse as naive UTC like the physical path, so the
                        # mixed lists can be sorted without tz-aware/naive errors.
                        obj.expireDate = parser.parse(
                            material["expectedRedeemDateUtc"], ignoretz=True
                        )
                        obj.createdDate = parser.parse(
                            material["createdDateUtc"], ignoretz=True
                        )
                        obj.pickupLibrary = "ereolen.dk"
                    except Exception as err:
                        _LOGGER.warning(
                            f"({self.user.date}) Skipping malformed eReolen reservation '{id}': {err}"
                        )
                        continue
                    erereservations.append(obj)
                self._reservations["ereolen"] = erereservations
            else:
                _LOGGER.warning(
                    f"({self.user.date}) Error fetching eReolen reservations ({res.status_code})"
                )
        else:
            self._reservations["ereolen"] = []
            self._reservations_ready["ereolen"] = []

        # Merge the last successful results from each source (see fetchLoans).
        self.user.reservations = (
            self._reservations["physical"] + self._reservations["ereolen"]
        )
        self.user.reservationsReady = (
            self._reservations_ready["physical"] + self._reservations_ready["ereolen"]
        )

    # Get debts, if any, from the Library
    def fetchDebts(self):
        debts = []
        params = {"includepaid": "false", "includenonpayable": "true"}
        res = self._get(URL_FEES, params=params, headers=self.json_header)
        if res.status_code == 200:
            js = res.json()
            for debt in js:
                try:
                    # A fee may have no materials attached (e.g. a service fee); keep
                    # the amount but leave the material fields empty.
                    materials = debt.get("materials") or []
                    data = (
                        self._getDetails(materials[0]["recordId"]) if materials else {}
                    )
                    obj = libraryDebt(data)

                    obj.feeDate = parser.parse(debt["creationDate"], ignoretz=True)
                    if debt.get("dueDate"):
                        obj.feeDueDate = parser.parse(debt["dueDate"], ignoretz=True)
                    obj.feeAmount = debt["amount"]
                    obj.feeType = debt.get("type")
                except Exception as err:
                    # One malformed fee must not abort the whole refresh.
                    _LOGGER.warning(
                        f"({self.user.date}) Skipping malformed fee entry: {err}"
                    )
                    continue
                debts.append(obj)
        else:
            _LOGGER.warning(
                f"({self.user.date}) Error fetching debts ({res.status_code})"
            )
            # Keep the previous debts so an outage does not show zero fees.
            return
        self.user.debts = debts
        self.user.debtsAmount = sum([float(obj.feeAmount) for obj in debts])
