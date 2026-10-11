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

from .const import branch_query, details_query
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

# Re-exported so existing imports (``from .library_api import libraryLoan``)
# and the public ``REQUEST_TIMEOUT`` constant keep working.
__all__ = [
    "DETAILS_CACHE_MAX",
    "REQUEST_TIMEOUT",
    "Library",
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

        self._transport = LibraryTransport(host, self.user)

    # The update function is called from the coordinator from Home Assistant
    def update(self):
        self._debug(
            f"Updating {self.use_eReolen}, {self.get_loans}, "
            f"{self.get_reservations}, {self.get_depts}"
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
            self.fetchUserInfo()
            if self.user.name:
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

    @property
    def library_token(self):
        return self._transport.library_token

    def login(self):
        return self._transport.login()

    def logout(self):
        self._transport.logout()

    # PRIVATE BEGIN ####
    def sortLists(self):
        # Sort the loans by expireDate and the Title
        self.user.loans.sort(
            key=lambda obj: (obj.expireDate is None, obj.expireDate, obj.title)
        )
        # Sort the reservations
        self.user.reservations.sort(
            key=lambda obj: (
                obj.queueNumber is None,
                obj.queueNumber,
                obj.createdDate is None,
                obj.createdDate,
                obj.title,
            )
        )
        # Sort the reservations
        self.user.reservationsReady.sort(
            key=lambda obj: (obj.pickupDate is None, obj.pickupDate, obj.title)
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
        res = self._post(
            "https://bibliotek.dk/api/bibdk21/graphql", headers=header, json=params
        )
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

    # Get information on the user
    def fetchUserInfo(self):
        # Fetch the user profile page
        res = self._get(
            "https://fbs-openplatform.dbc.dk/external/agencyid/patrons/patronid/v4",
            headers=self.json_header,
        )
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
            except (AttributeError, KeyError) as err:
                _LOGGER.error(f"Error getting user info {self.user.date}. Error: {err}")
        else:
            _LOGGER.warning(
                f"({self.user.date}) Error fetching user info ({res.status_code})"
            )

    # Get the loans with all possible details
    def fetchLoans(self):
        loans = []
        loansOverdue = []

        # Physical books
        res = self._get(
            "https://fbs-openplatform.dbc.dk/external/agencyid/patrons/patronid/loans/v2",
            headers=self.json_header,
        )
        if res.status_code == 200:
            for material in res.json():
                id = material["loanDetails"]["recordId"]
                data = self._getDetails(id)
                if data:
                    # Create an instance of libraryLoan
                    obj = libraryLoan(data)

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
                    if obj.expireDate < datetime.now():
                        loansOverdue.append(obj)
                    else:
                        loans.append(obj)
        else:
            _LOGGER.warning(
                f"({self.user.date}) Error fetching loans ({res.status_code})"
            )
        # Ebooks
        if self.use_eReolen:
            res = self._get(
                "https://pubhub-openplatform.dbc.dk/v1/user/loans",
                headers=self.json_header,
            )
            if res.status_code == 200:
                edata = res.json()

                self.user.eBooks = edata["userData"]["totalEbookLoans"]
                self.user.eBooksQuota = edata["libraryData"][
                    "maxConcurrentEbookLoansPerBorrower"
                ]
                self.user.audioBooks = edata["userData"]["totalAudioLoans"]
                self.user.audioBooksQuota = edata["libraryData"][
                    "maxConcurrentAudiobookLoansPerBorrower"
                ]

                for material in edata["loans"]:
                    id = material["libraryBook"]["identifier"]
                    res2 = self._get(
                        f"https://pubhub-openplatform.dbc.dk/v1/products/{id}",
                        headers=self.json_header,
                    )
                    if res2.status_code == 200:
                        data = res2.json()["product"]
                        obj = libraryLoan(data)

                        # Details
                        obj.id = id
                        obj.loanDate = parser.parse(
                            material["orderDateUtc"], ignoretz=True
                        )
                        obj.expireDate = parser.parse(
                            material["loanExpireDateUtc"], ignoretz=True
                        )
                        loans.append(obj)
                    else:
                        _LOGGER.warning(
                            f"({self.user.date}) Error fetching eReolen product '{id}' ({res2.status_code})"
                        )
            else:
                _LOGGER.warning(
                    f"({self.user.date}) Error fetching eReolen loans ({res.status_code})"
                )
        else:
            self.user.eBooks = 0
            self.user.eBooksQuota = 0
            self.user.audioBooks = 0
            self.user.audioBooksQuota = 0

        self.user.loans = loans
        self.user.loansOverdue = loansOverdue

    # Get the current reservations
    def fetchReservations(self):
        reservations = []
        reservationsReady = []

        # Physical books
        res = self._get(
            "https://fbs-openplatform.dbc.dk/external/v1/agencyid/patrons/patronid/reservations/v2",
            headers=self.json_header,
        )
        if res.status_code == 200:
            materials = {
                item["transactionId"]: item for item in res.json()
            }  # make sure only to take last if more than one item with same transaction
            for material in materials.values():
                id = material["recordId"]
                data = self._getDetails(id)
                if data:
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
                    if material["state"] == "readyForPickup":
                        obj.reservationNumber = material["pickupNumber"]
                        obj.pickupDate = parser.parse(
                            material["pickupDeadline"], ignoretz=True
                        )
                        reservationsReady.append(obj)
                    else:
                        obj.expireDate = parser.parse(
                            material["expiryDate"], ignoretz=True
                        )
                        obj.queueNumber = material["numberInQueue"]
                        reservations.append(obj)
        else:
            _LOGGER.warning(
                f"({self.user.date}) Error fetching reservations ({res.status_code})"
            )

        # eReolen
        if self.use_eReolen:
            res = self._get(
                "https://pubhub-openplatform.dbc.dk/v1/user/reservations",
                headers=self.json_header,
            )
            if res.status_code == 200:
                edata = res.json()
                for material in edata["reservations"]:
                    id = material["identifier"]
                    res2 = self._get(
                        f"https://pubhub-openplatform.dbc.dk/v1/products/{id}",
                        headers=self.json_header,
                    )
                    if res2.status_code == 200:
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
                        reservations.append(obj)
                    else:
                        _LOGGER.warning(
                            f"({self.user.date}) Error fetching eReolen product '{id}' ({res2.status_code})"
                        )
            else:
                _LOGGER.warning(
                    f"({self.user.date}) Error fetching eReolen reservations ({res.status_code})"
                )
        self.user.reservations = reservations
        self.user.reservationsReady = reservationsReady

    # Get debts, if any, from the Library
    def fetchDebts(self):
        debts = []
        params = {"includepaid": "false", "includenonpayable": "true"}
        res = self._get(
            "https://fbs-openplatform.dbc.dk/external/agencyid/patron/patronid/fees/v2",
            params=params,
            headers=self.json_header,
        )
        if res.status_code == 200:
            js = res.json()
            for debt in js:
                # A fee may have no materials attached (e.g. a service fee); keep
                # the amount but leave the material fields empty.
                materials = debt.get("materials") or []
                data = self._getDetails(materials[0]["recordId"]) if materials else {}
                obj = libraryDebt(data)

                obj.feeDate = parser.parse(debt["creationDate"], ignoretz=True)
                if debt.get("dueDate"):
                    obj.feeDueDate = parser.parse(debt["dueDate"], ignoretz=True)
                obj.feeAmount = debt["amount"]
                obj.feeType = debt.get("type")
                debts.append(obj)
        else:
            _LOGGER.warning(
                f"({self.user.date}) Error fetching debts ({res.status_code})"
            )
        self.user.debts = debts
        self.user.debtsAmount = sum([float(obj.feeAmount) for obj in debts])
