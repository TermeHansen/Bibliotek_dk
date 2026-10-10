from __future__ import annotations

from bs4 import BeautifulSoup as BS
from dateutil import parser
from datetime import timedelta, datetime
import logging
import re
import requests
from urllib3.util.retry import Retry

from .const import (
    HEADERS,
    JSON_HEADERS,
    URL_LOGIN_PAGE,
    details_query,
    branch_query,
)

# Timeout (connect, read) in seconds applied to every request, and the maximum
# number of entries kept in the material-details cache.
REQUEST_TIMEOUT = (10, 30)
DETAILS_CACHE_MAX = 500


_LOGGER = logging.getLogger(__name__)


class Library:
    host, libraryName, icon, user = None, None, None, None
    loggedIn = False
    use_eReolen, get_loans, get_reservations, get_depts = True, True, True, True

    def __init__(
        self,
        userId: str,
        pincode: str,
        host: str,
        agency: str,
        libraryName=None,
        use_eReolen: bool = True,
    ) -> None:

        self.session = self._new_session()

        self._json_header = JSON_HEADERS.copy()
        self._json_header["Origin"] = host
        self._json_header["Referer"] = host
        self._user_token = ""
        self._user_token_exp = None
        self._user_info_fetched = None
        self.branches = {}
        self._urls = {}
        self._details = {}
        self.loggedIn = ""

        self.host = host
        self.agency = agency
        self.user = libraryUser(userId=userId, pincode=pincode)
        self.user.date = self.user.userId[:-4]
        self.municipality = libraryName
        self.use_eReolen = use_eReolen

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

    # PRIVATE BEGIN ####
    def _new_session(self):
        """Build a session with default headers and transient-error retries."""
        session = requests.Session()
        session.headers = HEADERS
        # Retry connection errors and 5xx with backoff so a single hiccup does
        # not blank out every sensor for a whole poll cycle.
        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=(500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "POST"]),
        )
        adapter = requests.adapters.HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _debug(self, msg):
        # Gated by HA logging config: set this logger to DEBUG to see it.
        _LOGGER.debug(f"({self.user.date}) {msg}")

    def _get(self, url, **kwargs):
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        return self.session.get(url, **kwargs)

    def _post(self, url, **kwargs):
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        return self.session.post(url, **kwargs)

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

    # PRIVATE END  ####

    def login(self):
        if not self.loggedIn:
            url = self.host + URL_LOGIN_PAGE

            res = self._get(url)
            if res.status_code != 200:
                _LOGGER.error(f"({self.user.date}) Failed to login to {url}")
                return

            soup = BS(res.text, "html.parser")
            # Prepare the payload
            payload = {}
            # Find the <form>
            try:
                form = soup.find("form")
                for inputTag in form.find_all("input"):
                    # Fill the form with the userInfo
                    if inputTag["name"] in self.user.userInfo:
                        payload[inputTag["name"]] = self.user.userInfo[inputTag["name"]]
                    # or pass default values to payload
                    else:
                        payload[inputTag["name"]] = inputTag["value"]

                # Send the payload as POST and prepare a new soup
                # Use the URL from the response since we have been directed
                res2 = self._post(
                    form["action"].replace("/login", res.url), data=payload
                )
                res2.raise_for_status()

            #            except (AttributeError, KeyError) as err:
            except Exception as err:
                _LOGGER.error(
                    f"Error processing the <form> tag and subtags ({url}). Error: ({err})"
                )

        self._set_tokens()
        self._debug(f"is logged in: {self.loggedIn}")
        return self.loggedIn

    def _set_tokens(self):
        res = self._get(f"{self.host}/dpl-react/user-tokens")
        if res.status_code == 200:
            self._library_token = res.text.split('"library"')[1].split('"')[1]
            if '"user"' in res.text:
                self.loggedIn = self.host + "/logout"
                self._user_token = res.text.split('"user"')[1].split('"')[1]
                self._user_token_exp = datetime.now() + timedelta(days=1)
        else:
            _LOGGER.warning(
                f"({self.user.date}) Error fetching user tokens ({res.status_code})"
            )

    @property
    def json_header(self):
        self._json_header["Authorization"] = f"Bearer {self.user_token}"
        return self._json_header

    @property
    def user_token(self):
        now = datetime.now()
        if not self._user_token:
            self.login()
        if self._user_token_exp is not None and self._user_token_exp < now:
            self.loggedIn = ""
            self.login()
            _LOGGER.debug(f"new user token {self._user_token_exp}")
        return self._user_token

    @property
    def library_token(self):
        now = datetime.now()
        if not self._user_token:
            self.login()

        if self._user_token_exp is not None and self._user_token_exp < now:
            self.loggedIn = ""
            self.login()
        return self._library_token

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

    def logout(self):
        if self.loggedIn:
            url = self.loggedIn
            # Fetch the logout page, if given a 200 (true) reverse it to false
            self.loggedIn = self._get(url).status_code != 200
            if not self.loggedIn:
                self.session.close()
                self.session = self._new_session()
        self._debug(f"is logged OUT: {not self.loggedIn}")

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


class libraryUser:
    def __init__(self, userId: str, pincode: str) -> None:
        self.userInfo = {"loginBibDkUserId": userId, "pincode": pincode}
        self.userId = userId
        self.date = None
        self.name = None
        self.address = None
        self.phone = None
        self.phoneNotify = None
        self.mail = None
        self.mailNotify = None
        # Per-instance lists: class-level mutable defaults would be shared
        # between instances until each one assigns its own.
        self.loans = []
        self.loansOverdue = []
        self.reservations = []
        self.reservationsReady = []
        self.debts = []
        self.debtsAmount = 0.0
        self.eBooks = 0
        self.eBooksQuota = 0
        self.audioBooks = 0
        self.audioBooksQuota = 0
        self.pickupLibrary = None


class libraryMaterial:
    id = None
    type, title, creators = None, None, None
    url, coverUrl = None, None

    def __init__(self, data):
        try:
            if "thumbnailUri" in data:
                # from ereol
                self.coverUrl = data["thumbnailUri"]
                self.title = data["title"]
                self.creators = " og ".join(
                    [
                        item["firstName"] + item["lastName"]
                        for item in data["contributors"]
                    ]
                )
                self.type = data["format"]
                # The eReolen SPA has no server-side deep links, so link to a
                # bibliotek.dk search on the product's ISBN (externalProductId).
                isbn = (data.get("externalProductId") or {}).get("id")
                if isbn:
                    self.url = f"https://bibliotek.dk/?q={isbn}"
            elif "manifestation" in data:
                # physical book
                self.coverUrl = data["manifestation"]["cover"]["thumbnail"]
                self.title = data["manifestation"]["titles"]["full"][0]  # or main
                if data["manifestation"]["creators"]:
                    self.creators = data["manifestation"]["creators"][0]["display"]
                self.type = data["manifestation"]["materialTypes"][0][
                    "materialTypeSpecific"
                ]["display"]
                workId = (data["manifestation"].get("ownerWork") or {}).get("workId")
                if workId:
                    self.url = f"https://bibliotek.dk/work/{workId}"
        except Exception as err:
            _LOGGER.error(f"Failed to set material data, {err}")
            _LOGGER.error(f"{data}")


class libraryLoan(libraryMaterial):
    loanDate, expireDate = None, None
    renewId, renewAble = None, None


class libraryReservation(libraryMaterial):
    createdDate, expireDate, queueNumber = None, None, None
    pickupLibrary = None


class libraryReservationReady(libraryMaterial):
    createdDate, pickupDate, reservationNumber = None, None, None
    pickupLibrary = None


class libraryDebt(libraryMaterial):
    feeDate, feeDueDate, feeAmount, feeType = None, None, None, None
