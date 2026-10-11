"""HTTP transport, session, login and token handling for the Bibliotek client.

``LibraryTransport`` owns the ``requests`` session, the login flow and the
library/user tokens. It knows nothing about loans, reservations or debts — the
fetchers in ``library_api`` build on top of it.
"""

from __future__ import annotations

from bs4 import BeautifulSoup as BS
from datetime import timedelta, datetime
import logging
import requests
from urllib3.util.retry import Retry

from .const import HEADERS, JSON_HEADERS, URL_LOGIN_PAGE

# Timeout (connect, read) in seconds applied to every request.
REQUEST_TIMEOUT = (10, 30)


_LOGGER = logging.getLogger(__name__)


class LibraryTransport:
    """Session, login and token handling for a single library account."""

    def __init__(self, host: str, user) -> None:
        self.host = host
        self.user = user

        self.session = self._new_session()

        self._json_header = JSON_HEADERS.copy()
        self._json_header["Origin"] = host
        self._json_header["Referer"] = host
        self._user_token = ""
        self._user_token_exp = None
        self._library_token = ""
        self.loggedIn = ""

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
            # Return the final response instead of raising RetryError once the
            # status retries are exhausted, so the callers' status-code checks
            # and warnings still run (a single failing API must not abort the
            # whole update and blank every sensor).
            raise_on_status=False,
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

    def logout(self):
        if self.loggedIn:
            url = self.loggedIn
            # Fetch the logout page, if given a 200 (true) reverse it to false
            self.loggedIn = self._get(url).status_code != 200
            if not self.loggedIn:
                self.session.close()
                self.session = self._new_session()
        self._debug(f"is logged OUT: {not self.loggedIn}")
