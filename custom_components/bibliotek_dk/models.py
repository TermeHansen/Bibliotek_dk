"""Plain domain models for the Bibliotek integration.

These are intentionally dumb data holders: the fetchers in ``library_api``
populate them from the DBC payloads, and ``sensor`` reads them back. They hold
no HTTP or Home Assistant state.
"""

from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)


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
