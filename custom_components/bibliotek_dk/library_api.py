from __future__ import annotations

from bs4 import BeautifulSoup as BS
from dateutil import parser
from datetime import timedelta, datetime
import logging
import re
import requests

from .const import (
    HEADERS, JSON_HEADERS,
    URL_LOGIN_PAGE,
    details_query, branch_query,
)
DEBUG = True


_LOGGER: logging.Logger = logging.getLogger(__package__)
_LOGGER = logging.getLogger(__name__)


class Library:
    host, libraryName, icon, user = None, None, None, None
    loggedIn = False
    use_eReolen, get_loans, get_reservations, get_depts = True, True, True, True

    def __init__(
        self, userId: str, pincode: str, host: str, agency: str, libraryName=None
    ) -> None:

        self.session = requests.Session()
        self.session.headers = HEADERS

        self._json_header = JSON_HEADERS.copy()
        self._json_header["Origin"] = host
        self._json_header["Referer"] = host
        self._user_token = ''
        self.branches = {}
        self._urls = {}
        self._details = {}
        self.loggedIn = ''

        self.host = host
        self.agency = agency
        self.user = libraryUser(userId=userId, pincode=pincode)
        self.user.date = self.user.userId[:-4]
        self.municipality = libraryName

    # The update function is called from the coordinator from Home Assistant
    def update(self):
        _LOGGER.debug(f"Updating ({self.user.date}) {self.use_eReolen}, {self.get_loans}, {self.get_reservations}, {self.get_depts}")

        # Only fetch user info once
        if not self.user.name:
            self._branchName(self.agency)
            self.fetchUserInfo()

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
    def sortLists(self):
        # Sort the loans by expireDate and the Title
        self.user.loans.sort(key=lambda obj: (obj.expireDate is None, obj.expireDate, obj.title))
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
        self.user.reservationsReady.sort(key=lambda obj: (obj.pickupDate is None, obj.pickupDate, obj.title))

    def _branchName(self, id):
        id = str(id).split('-')[-1]
        if id in self.branches:
            return self.branches[id]

        params = {
            'query': branch_query,
            'variables': {'language': "DA", 'limit': 50, 'q': id}
        }
        header = {'Accept': '*/*'}
        res = self.session.post("https://bibliotek.dk/api/bibdk21/graphql", headers=header, json=params)
        if res.status_code == 200:
            data = res.json()['data']['branches']
            for branch in data['result']:
                self.branches[branch['branchId']] = branch['name']
        return self.branches.get(id, id)

    def _getDetails(self, faust):
        if faust in self._details:
            return self._details[faust]
        data = {}
        params = {"query": details_query, "variables": {"faust": faust}}
        url = self.urls.get('data-fbi-global-base-url', "https://temp.fbi-api.dbc.dk/next-present/graphql")
        res = self.session.post(url, headers=self.json_header, json=params)
        if res.status_code == 200:
            data = res.json()['data']
            self._details[faust] = data
        else:
            _LOGGER.error(f"Error getting details for material: '{faust}'")
        return self._details[faust]

    # PRIVATE END  ####

    def login(self):
        if not self.loggedIn:
            url = self.host + URL_LOGIN_PAGE

            res = self.session.get(url)
            if res.status_code != 200:
                _LOGGER.error("f({self.user.date}) Failed to login to {url}")
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
                res2 = self.session.post(form["action"].replace("/login", res.url), data=payload)
                res2.raise_for_status()

#            except (AttributeError, KeyError) as err:
            except Exception as err:
                _LOGGER.error(f"Error processing the <form> tag and subtags ({url}). Error: ({err})")

        self._set_tokens()
        if DEBUG:
            _LOGGER.debug("(%s) is logged in: %s", self.user.date, self.loggedIn)
        return self.loggedIn

    def _set_tokens(self):
        res = self.session.get(f"{self.host}/dpl-react/user-tokens")
        if res.status_code == 200:
            self._library_token = res.text.split('"library"')[1].split('"')[1]
            if '"user"' in res.text:
                self.loggedIn = self.host + '/logout'
                self._user_token = res.text.split('"user"')[1].split('"')[1]
                self._user_token_exp = datetime.now() + timedelta(days=7)

    @property
    def json_header(self):
        self._json_header["Authorization"] = f"Bearer {self.user_token}"
        return self._json_header

    @property
    def user_token(self):
        now = datetime.now() - timedelta(days=1)
        if not self._user_token or self._user_token_exp < now:
            self.login()
            # _LOGGER.error(f'new user token {self._user_token_exp}')
        return self._user_token

    @property
    def library_token(self):
        now = datetime.now() - timedelta(days=1)
        if not self._user_token or self._user_token_exp < now:
            self.login()
            # _LOGGER.error(f'new library token {self._user_token_exp}')
        return self._library_token

    @property
    def urls(self):
        if not self._urls:
            res = self.session.get(f'{self.host}/user/me/loans')
            if res.status_code == 200:
                self._urls = {m[0]: m[1] for m in re.findall(r'(data-[a-zA-Z0-9\-\_]+-url)="([^"]*)"', res.text)}
        return self._urls

    def logout(self):
        if self.loggedIn:
            url = self.loggedIn
            # Fetch the logout page, if given a 200 (true) reverse it to false
            self.loggedIn = not self.session.get(url).status_code == 200
            if not self.loggedIn:
                self.session.close()
                self.session = requests.Session()
                self.session.headers = HEADERS
        if DEBUG:
            _LOGGER.debug(f"({self.user.date}) is logged OUT @{url}: {~self.loggedIn}")

    # Get information on the user
    def fetchUserInfo(self):
        # Fetch the user profile page
        res = self.session.get('https://fbs-openplatform.dbc.dk/external/agencyid/patrons/patronid/v4', headers=self.json_header)
        if res.status_code == 200:
            try:
                data = res.json()['patron']

                self.user.name = data['name']
                self.user.address = f'{data["address"]["street"]}\n{data["address"]["postalCode"]} {data["address"]["city"]}'
                self.user.phone = data['phoneNumber']
                self.user.phoneNotify = int(data['receiveSms'])
                self.user.mail = data['emailAddress']
                self.user.mailNotify = int(data['receiveEmail'])
                self.user.pickupLibrary = self._branchName(data['preferredPickupBranch'])
                self.libraryName = self._branchName(data['preferredPickupBranch'])
            except (AttributeError, KeyError) as err:
                _LOGGER.error(f"Error getting user info {self.user.dat}. Error: {err}")

    # Get the loans with all possible details
    def fetchLoans(self):
        loans = []
        loansOverdue = []

        # Physical books
        res = self.session.get("https://fbs-openplatform.dbc.dk/external/agencyid/patrons/patronid/loans/v2", headers=self.json_header)
        if res.status_code == 200:
            for material in res.json():
                id = material['loanDetails']['recordId']
                data = self._getDetails(id)
                if data:
                    # Create an instance of libraryLoan
                    obj = libraryLoan(data)

                    # Renewable
                    obj.renewId = material['loanDetails']['loanId']
                    obj.renewAble = material['isRenewable']
                    obj.loanDate = parser.parse(material['loanDetails']['loanDate'], ignoretz=True)
                    obj.expireDate = parser.parse(material['loanDetails']['dueDate'], ignoretz=True) + timedelta(hours=23, minutes=59)
                    obj.id = material['loanDetails']['materialItemNumber']
                    if obj.expireDate < datetime.now():
                        loansOverdue.append(obj)
                    else:
                        loans.append(obj)
        # Ebooks
        if self.use_eReolen:
            res = self.session.get('https://pubhub-openplatform.dbc.dk/v1/user/loans', headers=self.json_header)
            if res.status_code == 200:
                edata = res.json()

                self.user.eBooks = edata['userData']['totalEbookLoans']
                self.user.eBooksQuota = edata['libraryData']['maxConcurrentEbookLoansPerBorrower']
                self.user.audioBooks = edata['userData']['totalAudioLoans']
                self.user.audioBooksQuota = edata['libraryData']['maxConcurrentAudiobookLoansPerBorrower']

                for material in edata['loans']:
                    id = material['libraryBook']['identifier']
                    res2 = self.session.get(f'https://pubhub-openplatform.dbc.dk/v1/products/{id}', headers=self.json_header)
                    if res2.status_code == 200:
                        data = res2.json()['product']
                        obj = libraryLoan(data)

                        # Details
                        obj.id = id
                        obj.loanDate = parser.parse(material['orderDateUtc'], ignoretz=True)
                        obj.expireDate = parser.parse(material['loanExpireDateUtc'], ignoretz=True)
                        loans.append(obj)
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
        res = self.session.get("https://fbs-openplatform.dbc.dk/external/v1/agencyid/patrons/patronid/reservations/v2", headers=self.json_header)
        materials = {item['transactionId']: item for item in res.json()}  # make sure only to take last if more than one item with same transaction
        for material in materials.values():
            id = material['recordId']
            data = self._getDetails(id)
            if data:
                if material['state'] == 'readyForPickup':
                    obj = libraryReservationReady(data)
                else:
                    obj = libraryReservation(data)

                # Details
                obj.id = id
                obj.createdDate = parser.parse(material['dateOfReservation'], ignoretz=True)
                obj.pickupLibrary = self._branchName(material['pickupBranch'])
                if material['state'] == 'readyForPickup':
                    obj.reservationNumber = material['pickupNumber']
                    obj.pickupDate = parser.parse(material['pickupDeadline'], ignoretz=True)
                    reservationsReady.append(obj)
                else:
                    obj.expireDate = parser.parse(material['expiryDate'], ignoretz=True)
                    obj.queueNumber = material['numberInQueue']
                    reservations.append(obj)

        # eReolen
        if self.use_eReolen:
            res = self.session.get("https://pubhub-openplatform.dbc.dk/v1/user/reservations", headers=self.json_header)
            if res.status_code == 200:
                edata = res.json()
                for material in edata['reservations']:
                    _LOGGER.debug(f"E-reol reservering data {material}")
                    id = material['identifier']
                    res2 = self.session.get(f'https://pubhub-openplatform.dbc.dk/v1/products/{id}', headers=self.json_header)
                    if res2.status_code == 200:
                        data = res2.json()['product']
                        _LOGGER.debug(f"E-reol reservering data {res.json()}")

                        obj = libraryReservation(data)
                        obj.id = id

                        obj.expireDate = parser.parse(material['expectedRedeemDateUtc'])
                        obj.createdDate = parser.parse(material['createdDateUtc'])
                        obj.pickupLibrary = 'ereolen.dk'
                        reservations.append(obj)
        self.user.reservations = reservations
        self.user.reservationsReady = reservationsReady

    # Get debts, if any, from the Library
    def fetchDebts(self):
        debts = []
        params = {'includepaid': 'false', 'includenonpayable': 'true'}
        res = self.session.get("https://fbs-openplatform.dbc.dk/external/agencyid/patron/patronid/fees/v2", params=params, headers=self.json_header)
        if res.status_code == 200:
            js = res.json()
            for debt in js:
                # TODO more than one material?
                material = debt['materials'][0]
                id = material['recordId']
                data = self._getDetails(id)
                if data:
                    obj = libraryDebt(data)

                    obj.feeDate = parser.parse(debt['creationDate'], ignoretz=True)
                    obj.feeDueDate = parser.parse(debt['dueDate'], ignoretz=True)
                    obj.feeAmount = debt['amount']
                    debts.append(obj)
        self.user.debts = debts
        self.user.debtsAmount = sum([float(obj.feeAmount) for obj in debts])


class libraryUser:
    userInfo = None
    name, address = None, None
    phone, phoneNotify, mail, mailNotify = None, None, None, None
    loans, loansOverdue, reservations, reservationsReady, debts = [], [], [], [], []
    debtsAmount = 0.0
    eBooks, eBooksQuota, audioBooks, audioBooksQuota = 0, 0, 0, 0
    pickupLibrary = None

    def __init__(self, userId: str, pincode: str) -> None:
        self.userInfo = {"loginBibDkUserId": userId, "pincode": pincode}
        self.userId = userId


class libraryMaterial:
    id = None
    type, title, creators = None, None, None
    url, coverUrl = None, None

    def __init__(self, data):
        try:
            if 'thumbnailUri' in data:
                # from ereol
                self.coverUrl = data['thumbnailUri']
                self.title = data['title']
                self.creators = ' og '.join([item['firstName'] + item['lastName'] for item in data['contributors']])
                self.type = data['format']
            elif 'manifestation' in data:
                # physical book
                self.coverUrl = data['manifestation']['cover']['thumbnail']
                self.title = data['manifestation']['titles']['full'][0]  # or main
                if data['manifestation']['creators']:
                    self.creators = data['manifestation']['creators'][0]['display']
                self.type = data['manifestation']['materialTypes'][0]['materialTypeSpecific']['display']
        except Exception as err:
            _LOGGER.error(f'Failed to set material data, {err}')
            _LOGGER.error(f'{data}')


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
    feeDate, feeDueDate, feeAmount = None, None, None
